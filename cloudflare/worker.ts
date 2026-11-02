/**
 * Cloudflare Workers の入口。OpenNext が作った worker を包む。
 *
 * Workers ではリポジトリのファイル（記事 JSON・設定・フォント）を fs で読めないので、
 * ビルド時に静的アセット（/_content/*、scripts/cf_content.mjs が作る）へ置いたものを
 * リクエストの前に先読みし、lib/content-fs.ts が読む仮想 fs（globalThis.__SAISOKU_VFS__）に入れる。
 * デプロイごとに中身は固定なので、isolate の寿命の間は使い回す。
 */
// @ts-ignore ビルド時に生成される
import { default as handler } from "../.open-next/worker.js";
import { flushHeld, handlePush, type PushEnv } from "./push";
import { handleAff, type AffEnv } from "./aff";
// @ts-ignore ビルド時に生成される（scripts/cf_content.mjs。JS・CSS のファイル名の組のハッシュ）
import codeInfo from "../.open-next/saisoku-code.json";

type Env = { ASSETS: { fetch: (input: string | Request) => Promise<Response> } } & Record<string, unknown>;

type VirtualFs = {
  files: Map<string, string | Uint8Array>;
  dirs: Map<string, string[]>;
  roots: string[];
};

type Base = { files: Record<string, string>; dirs: Record<string, string[]> };

const g = globalThis as { __SAISOKU_VFS__?: VirtualFs };
const ORIGIN = "https://assets.local";
const FULL_LIMIT = 200;

let baseLoading: Promise<void> | null = null;
let fontsLoading: Promise<void> | null = null;
/** 本文まで読み込んだ記事（古いものから捨てる） */
const fullSlugs: string[] = [];

async function asset(env: Env, pathname: string): Promise<Response> {
  return env.ASSETS.fetch(new Request(`${ORIGIN}${pathname}`));
}

function loadBase(env: Env): Promise<void> {
  baseLoading ??= (async () => {
    const res = await asset(env, "/_content/base.json");
    if (!res.ok) throw new Error(`content assets missing: ${res.status}`);
    const base = (await res.json()) as Base;
    g.__SAISOKU_VFS__ = {
      files: new Map(Object.entries(base.files)),
      dirs: new Map(Object.entries(base.dirs)),
      roots: ["content", "affiliate_links.json", "monetization.json", "assets/fonts"],
    };
  })().catch((error) => {
    baseLoading = null;
    throw error;
  });
  return baseLoading;
}

function loadFonts(env: Env): Promise<void> {
  fontsLoading ??= (async () => {
    const list = g.__SAISOKU_VFS__!.dirs.get("assets/fonts") ?? [];
    await Promise.all(
      list.map(async (name) => {
        const res = await asset(env, `/_content/fonts/${name}`);
        if (!res.ok) throw new Error(`font missing: ${name}`);
        g.__SAISOKU_VFS__!.files.set(`assets/fonts/${name}`, new Uint8Array(await res.arrayBuffer()));
      }),
    );
  })().catch((error) => {
    fontsLoading = null;
    throw error;
  });
  return fontsLoading;
}

/** 記事ページ・カバーを描く前に、その記事の本文入り JSON を読み込む（無い記事は 404 のまま） */
async function loadArticle(env: Env, slug: string): Promise<void> {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(slug) || fullSlugs.includes(slug)) return;
  const res = await asset(env, `/_content/articles/${slug}.json`);
  if (res.status === 404) return;
  if (!res.ok) throw new Error(`article asset failed: ${slug} ${res.status}`);
  const vfs = g.__SAISOKU_VFS__!;
  vfs.files.set(`content/articles/${slug}.json`, await res.text());
  fullSlugs.push(slug);
  while (fullSlugs.length > FULL_LIMIT) vfs.files.delete(`content/articles/${fullSlugs.shift()}.json`);
}

/** wrangler.jsonc の triggers.crons と同じ文字列（UTC 22:00 = 日本時間 7:00） */
const PUSH_MORNING_CRON = "0 22 * * *";

const ARTICLE = /^\/(?:articles|cover|card)\/([^/]+?)(?:\.rsc)?\/?$/;

type Ctx = { waitUntil(promise: Promise<unknown>): void };

/**
 * 出来上がったページを Cloudflare のエッジ（Cache API）に置き、次からは描画もキャッシュ読み出しもせずに返す。
 * キーはデプロイの版ではなく「JS・CSS のファイル名の組」（scripts/cf_content.mjs が作る CODE_HASH）にする。
 * 記事だけ増えたデプロイ（1日に数十回）ではファイル名が変わらないので、前の版の HTML をそのまま返しつつ
 * 裏で作り直す（stale-while-revalidate）。デプロイのたびに全ページが各地のエッジで空振りして TTFB が伸びていた。
 * JS・CSS が変わったデプロイではキーが変わるので、消えた JS を参照する古い HTML は返らない。
 * 画面遷移用の RSC リクエストは対象外。workers.dev では Cache API が効かない（独自ドメインでだけ効く）
 */
function deployVersion(env: Env): string {
  return (env.CF_VERSION_METADATA as { id?: string } | undefined)?.id ?? "dev";
}

function edgeCacheKey(request: Request, url: URL, env: Env): Request | null {
  if (request.method !== "GET" || request.headers.has("rsc") || url.searchParams.has("_rsc")) return null;
  if (url.pathname.startsWith("/_next/") || url.pathname.startsWith("/api/")) return null;
  const key = new URL(url);
  key.searchParams.set("__c", (codeInfo as { hash?: string }).hash || deployVersion(env));
  return new Request(key.toString(), { method: "GET" });
}

/** 作り直し中のキー（同じ isolate で重ねて描かない） */
const refreshing = new Set<string>();

/** ISR・静的化されたものだけ（初回の描画中の no-store や、動的なものは置かない） */
function edgeCacheable(response: Response): boolean {
  const cc = response.headers.get("cache-control") ?? "";
  if (response.status !== 200 || /private|no-store/.test(cc) || response.headers.has("set-cookie")) return false;
  return /s-maxage=\d+/.test(cc) || response.headers.has("x-nextjs-cache");
}

/** エッジには1日置く（JS・CSS が変われば自然に使われなくなる）。ブラウザには毎回確かめさせる。どの版で描いたかを残す */
function forEdge(response: Response, env: Env): Response {
  const copy = new Response(response.body, response);
  const image = (copy.headers.get("content-type") ?? "").startsWith("image/");
  copy.headers.set("cache-control", image ? "public, max-age=604800, s-maxage=86400" : "public, max-age=0, s-maxage=86400");
  copy.headers.set("x-saisoku-ver", deployVersion(env));
  return copy;
}

type KvNamespace = { get(key: string, type: "arrayBuffer"): Promise<ArrayBuffer | null> };

const IMAGE_NAME = /^\/img\/(([A-Za-z0-9][A-Za-z0-9_-]{0,160})\.(jpg|sm\.webp|w(?:400|800|1200)\.webp))$/;

/**
 * 記事の写真（フリー素材・AI 生成・公式発表の写真。scripts/images.py が Workers KV の saisoku-images に置く）。
 * 名前に中身のハッシュが入っていて変わらないので、ブラウザ・エッジとも1年使い回す。
 * エッジのキャッシュはデプロイの版に関係なく使う（KV の読み出しはキャッシュが無いときだけ）。
 * 幅ごとの WebP（.w400 / .w800 / .w1200、lib/thumb-url.ts の srcset）がまだ無い古い写真は、元の .jpg を短いキャッシュで返す
 */
async function serveImage(request: Request, url: URL, env: Env, ctx: Ctx): Promise<Response> {
  const match = IMAGE_NAME.exec(url.pathname);
  if (!match || (request.method !== "GET" && request.method !== "HEAD")) return new Response("Not Found", { status: 404 });
  const edge = (caches as unknown as { default: Cache }).default;
  const key = new Request(`${url.origin}${url.pathname}`, { method: "GET" });
  let response = await edge.match(key);
  if (!response) {
    const kv = env.IMAGES_KV as KvNamespace | undefined;
    let body = kv ? await kv.get(match[1], "arrayBuffer") : null;
    let type = match[3] === "jpg" ? "image/jpeg" : "image/webp";
    let cache = "public, max-age=31536000, immutable";
    if (!body && kv && match[3].startsWith("w")) {
      body = await kv.get(`${match[2]}.jpg`, "arrayBuffer");
      type = "image/jpeg";
      cache = "public, max-age=600";
    }
    if (!body) return new Response("Not Found", { status: 404, headers: { "Cache-Control": "public, max-age=60" } });
    response = new Response(body, {
      headers: {
        "Content-Type": type,
        "Cache-Control": cache,
        "Access-Control-Allow-Origin": "*",
      },
    });
    ctx.waitUntil(edge.put(key, response.clone()));
  }
  return request.method === "HEAD" ? new Response(null, { headers: response.headers }) : response;
}

const VIDEO_NAME = /^\/video\/([A-Za-z0-9][A-Za-z0-9_-]{0,160}\.mp4)$/;

/**
 * SNS 用の解説動画（scripts/sns_video_followup.py が Workers KV の saisoku-images に「video/<slug>-<hash>.mp4」で置く。1本 25MiB 未満）。
 * 名前に中身のハッシュが入っていて変わらないので1年使い回す。エッジには全体（200）を置き、
 * Range（bytes=a-b）の要求にはそこから切り出して 206 で返す（動画プレーヤーや SNS の取り込みが Range を使うため）
 */
async function serveVideo(request: Request, url: URL, env: Env, ctx: Ctx): Promise<Response> {
  const match = VIDEO_NAME.exec(url.pathname);
  if (!match || (request.method !== "GET" && request.method !== "HEAD")) return new Response("Not Found", { status: 404 });
  const edge = (caches as unknown as { default: Cache }).default;
  const key = new Request(`${url.origin}${url.pathname}`, { method: "GET" });
  let full = await edge.match(key);
  if (!full) {
    const kv = env.IMAGES_KV as KvNamespace | undefined;
    const body = kv ? await kv.get(`video/${match[1]}`, "arrayBuffer") : null;
    if (!body) return new Response("Not Found", { status: 404, headers: { "Cache-Control": "public, max-age=60" } });
    full = new Response(body, {
      headers: {
        "Content-Type": "video/mp4",
        "Content-Length": String(body.byteLength),
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=31536000, immutable",
        "Access-Control-Allow-Origin": "*",
      },
    });
    ctx.waitUntil(edge.put(key, full.clone()));
  }
  const headers = new Headers(full.headers);
  const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.get("Range") || "");
  if (!range || (range[1] === "" && range[2] === "")) {
    return request.method === "HEAD" ? new Response(null, { headers }) : new Response(full.body, { headers });
  }
  const buf = await full.arrayBuffer();
  const size = buf.byteLength;
  const start = range[1] === "" ? Math.max(0, size - Number(range[2])) : Number(range[1]);
  const end = range[1] === "" || range[2] === "" ? size - 1 : Math.min(Number(range[2]), size - 1);
  if (start >= size || start > end) {
    return new Response(null, { status: 416, headers: { "Content-Range": `bytes */${size}`, "Accept-Ranges": "bytes" } });
  }
  headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
  headers.set("Content-Length", String(end - start + 1));
  return new Response(request.method === "HEAD" ? null : buf.slice(start, end + 1), { status: 206, headers });
}

/** 記事 JSON などを先読みしてから OpenNext（Next）で描く */
async function render(request: Request, url: URL, env: Env, ctx: Ctx): Promise<Response> {
  // 静的ファイルは OpenNext/アセットに任せる（先読み不要）
  if (!url.pathname.startsWith("/_next/static/")) {
    try {
      await loadBase(env);
      const match = ARTICLE.exec(url.pathname);
      if (match) await loadArticle(env, decodeURIComponent(match[1]));
      // 記事デザイン案・新コンセプトの確認用ページ（?slug=。既定は app/article-preview/variants.ts・app/article-concept/page.tsx と同じ記事）
      if (url.pathname.startsWith("/article-preview/") || url.pathname === "/article-concept" || url.pathname.startsWith("/article-concept/")) {
        await loadArticle(env, url.searchParams.get("slug") || "openai-gpt-6-1-terra-announcement");
      }
      // 朝のまとめのシェア画像（app/digest/[id]/share.png・card.png）もカバーと同じフォントで描く
      if (url.pathname.startsWith("/cover/") || url.pathname.startsWith("/card/") || /^\/digest\/\d{8}\/(share|card)\.png$/.test(url.pathname)) await loadFonts(env);
    } catch (error) {
      console.error("[worker] content preload failed", error);
      return new Response("Service Unavailable", { status: 503, headers: { "Retry-After": "5" } });
    }
  }
  return handler.fetch(request, env, ctx);
}

export default {
  async fetch(request: Request, env: Env, ctx: Ctx): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/_content/")) return new Response("Not Found", { status: 404 });
    if (url.pathname.startsWith("/img/")) return serveImage(request, url, env, ctx);
    if (url.pathname.startsWith("/video/")) return serveVideo(request, url, env, ctx);
    // 速報のプッシュ通知（cloudflare/push.ts。Next を通さずここで返す）
    if (url.pathname.startsWith("/api/push/")) return handlePush(request, url, env as unknown as PushEnv, ctx);
    // 提携リンクのクリックの自前集計（cloudflare/aff.ts）
    if (url.pathname.startsWith("/api/aff/")) return handleAff(request, url, env as unknown as AffEnv, ctx);

    const cacheKey = edgeCacheKey(request, url, env);
    const edge = cacheKey ? (caches as unknown as { default: Cache }).default : null;
    if (edge && cacheKey) {
      const hit = await edge.match(cacheKey);
      if (hit) {
        const fresh = hit.headers.get("x-saisoku-ver") === deployVersion(env);
        // 前のデプロイで描いたもの: すぐ返し、裏で今の版に作り直す
        if (!fresh && !refreshing.has(cacheKey.url)) {
          refreshing.add(cacheKey.url);
          ctx.waitUntil(
            render(new Request(request.url, { method: "GET", headers: request.headers }), url, env, ctx)
              .then(async (response) => {
                if (edgeCacheable(response)) await edge.put(cacheKey, forEdge(response, env));
              })
              .catch((error) => console.error("[worker] refresh failed", url.pathname, String(error)))
              .finally(() => refreshing.delete(cacheKey.url)),
          );
        }
        const out = new Response(hit.body, hit);
        out.headers.set("x-saisoku-edge", fresh ? "hit" : "stale");
        return out;
      }
    }

    const response = await render(request, url, env, ctx);
    if (edge && cacheKey && edgeCacheable(response)) {
      const cached = forEdge(response, env);
      ctx.waitUntil(edge.put(cacheKey, cached.clone()));
      cached.headers.set("x-saisoku-edge", "miss");
      return cached;
    }
    return response;
  },

  /** Cron（wrangler.jsonc の triggers）。毎朝 7:00（日本時間）に夜のあいだ保留した速報の通知を1本送る */
  async scheduled(event: { cron: string }, env: Env, ctx: Ctx): Promise<void> {
    if (event.cron === PUSH_MORNING_CRON) {
      ctx.waitUntil(
        flushHeld(env as unknown as PushEnv).then(
          (result) => console.log("[push] morning", JSON.stringify(result)),
          (error) => console.error("[push] morning failed", String(error)),
        ),
      );
    }
  },
};

// @ts-ignore ビルド時に生成される
export { DOQueueHandler } from "../.open-next/worker.js";
