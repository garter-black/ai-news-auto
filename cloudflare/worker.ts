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
import { serveVideo } from "./video";
import { handleReview, type ReviewEnv } from "./review";
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

/**
 * 取り下げた記事（content/retired.json。scripts/retire_articles.py が書き、scripts/cf_content.mjs が /_content/retired.json に置く）。
 * 重複の記事は代表の記事へ 301、それ以外は 410 Gone を返す（AdSense「有用性の低いコンテンツ」への対策。2026-11-03）
 */
type RetiredEntry = { reason: string; redirect?: string };
type RetiredLists = { articles: Map<string, RetiredEntry>; products: Map<string, RetiredEntry> };
let retiredLoading: Promise<RetiredLists> | null = null;

function loadRetiredLists(env: Env): Promise<RetiredLists> {
  const empty = (): RetiredLists => ({ articles: new Map(), products: new Map() });
  retiredLoading ??= (async () => {
    const res = await asset(env, "/_content/retired.json");
    if (!res.ok) return empty();
    const data = (await res.json()) as { articles?: Record<string, RetiredEntry>; products?: Record<string, RetiredEntry> };
    return { articles: new Map(Object.entries(data.articles ?? {})), products: new Map(Object.entries(data.products ?? {})) };
  })().catch((error) => {
    retiredLoading = null;
    console.error("[worker] retired list failed", String(error));
    return empty();
  });
  return retiredLoading;
}

async function loadRetired(env: Env): Promise<Map<string, RetiredEntry>> {
  return (await loadRetiredLists(env)).articles;
}

/**
 * 消えた製品ページ（content/retired.json の products。scripts/retire_articles.py が、関係する記事・出典を失った製品を消すときに書く）。
 * 404 にせず、親の製品ページ（redirect。例: chatgpt-enterprise → chatgpt）へ、無ければ図鑑の一覧（/products）へ 301（台帳 W30。2026-11-04）。
 * 同じ slug の製品ページがまた作られていれば（content/products にある）転送しない
 */
const PRODUCT_PATH = /^\/products\/([^/]+?)(\.rsc)?\/?$/;

async function retiredProductResponse(url: URL, env: Env): Promise<Response | null> {
  const match = PRODUCT_PATH.exec(url.pathname);
  if (!match) return null;
  let slug: string;
  try {
    slug = decodeURIComponent(match[1]);
  } catch {
    return null;
  }
  const entry = (await loadRetiredLists(env)).products.get(slug);
  if (!entry) return null;
  try {
    await loadBase(env);
    if ((g.__SAISOKU_VFS__?.dirs.get("content/products") ?? []).includes(`${slug}.json`)) return null;
  } catch {
    // 中身の一覧が読めないときも転送する（消えた製品なので 404 よりよい）
  }
  const target = entry.redirect && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(entry.redirect) && entry.redirect !== slug ? `/products/${entry.redirect}` : "/products";
  const to = new URL(`${target}${match[2] ?? ""}`, url);
  to.search = url.search;
  return new Response(null, { status: 301, headers: { Location: to.pathname + to.search, "Cache-Control": "public, max-age=3600" } });
}

const RETIRED_PATH = /^\/(articles|cover|card)\/([^/]+?)(\.rsc)?\/?$/;

const GONE_HTML = `<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>この記事は掲載を終了しました｜サイソクニュース</title><style>body{margin:0;background:#f3f5f8;color:#0b1a33;font:16px/1.8 system-ui,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}main{max-width:640px;margin:0 auto;padding:48px 16px}h1{font-size:22px;margin:0 0 12px}a{color:#1d4a8f}ul{padding-left:1.2em}</style></head><body><main><p style="color:#1d4a8f;font-weight:700;margin:0">サイソクニュース</p><h1>この記事は掲載を終了しました</h1><p>サイソクニュースでは、読者の方に役立つ記事を中心にお届けするため、記事の整理を行っています。この記事はその中で掲載を終了しました。ご不便をおかけし、申し訳ありません。</p><ul><li><a href="/">トップページ</a></li><li><a href="/articles">新着記事</a></li><li><a href="/editorial-policy">編集方針</a></li></ul></main></body></html>`;

/**
 * Next の読み込み用スクリプトを外した HTML（410 のページは画面を動かさない静的なページにする）。
 * 状態コードが 200 以外の文書をブラウザでハイドレーションすると、同じ HTML でも React のエラー（#418）が出るため。
 * リンク・検索窓（ただのフォーム）・見た目はそのまま。テーマの初期化などの小さなインラインスクリプトは残す
 */
function staticHtml(html: string): string {
  return html
    .replace(/<script[^>]*\bsrc="\/_next\/[^"]*"[^>]*><\/script>/g, "")
    .replace(/<script[^>]*>\s*\(?self\.__next_f[\s\S]*?<\/script>/g, "")
    .replace(/<script id="_R_"[^>]*>[\s\S]*?<\/script>/g, "")
    .replace(/<link[^>]+as="script"[^>]*>/g, "");
}

/**
 * 取り下げた記事の 410 ページ。サイトのヘッダー・フッター・色のまま（同じ URL を Next に描かせ、記事ページが
 * components/GoneView.tsx を出す。状態コードだけ 410 にする。2026-11-03 オーナー決定・台帳 W22）。
 * 別の URL（/gone/…）で描くと、ブラウザの URL と描いたページがずれてハイドレーションのエラーになる。描けないときだけ上の素の HTML
 */
async function goneResponse(request: Request, url: URL, env: Env, ctx: Ctx, rsc: boolean): Promise<Response> {
  const headers = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex" };
  if (request.method === "HEAD" || rsc) return new Response(null, { status: 410, headers });
  try {
    const page = await render(new Request(request.url, { method: "GET", headers: request.headers }), url, env, ctx);
    if (page.status === 200 && (page.headers.get("content-type") ?? "").includes("text/html")) {
      const html = await page.text();
      if (html.includes("data-gone-page")) return new Response(staticHtml(html), { status: 410, headers });
    }
  } catch (error) {
    console.error("[worker] gone page failed", url.pathname, String(error));
  }
  return new Response(GONE_HTML, { status: 410, headers });
}

async function retiredResponse(request: Request, url: URL, env: Env, ctx: Ctx): Promise<Response | null> {
  const match = RETIRED_PATH.exec(url.pathname);
  if (!match) return null;
  let slug: string;
  try {
    slug = decodeURIComponent(match[2]);
  } catch {
    return null;
  }
  const entry = (await loadRetired(env)).get(slug);
  if (!entry) return null;
  if (entry.redirect && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(entry.redirect)) {
    const to = new URL(`/${match[1]}/${entry.redirect}${match[3] ?? ""}`, url);
    to.search = url.search;
    return new Response(null, { status: 301, headers: { Location: to.pathname + to.search, "Cache-Control": "public, max-age=3600" } });
  }
  if (match[1] !== "articles") {
    return new Response(null, { status: 410, headers: { "Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex" } });
  }
  return goneResponse(request, url, env, ctx, Boolean(match[3]) || request.headers.has("rsc"));
}

/**
 * 終了した固定ページ（記事以外）。410 Gone を返し、検索エンジンに「もう無い」と伝える。
 * /tools/ai-plan-cost … AIの有料プランの費用計算。足し算・割り算だけで読者に新しい情報が無かったため 2026-11-03 に終了
 * /tools/usd-jpy … ドル建てサブスクの円換算。オーナー「このページもいらない」（2026-11-03・台帳 W12）。料金の段の表の円の目安と重なる
 * （AdSense「有用性の低いコンテンツ」への対策。ページ・計算機・サイトマップ・記事への埋め込みは消してある）
 */
const RETIRED_PAGES: Record<string, { title: string; text: string; links: [string, string][] }> = {
  "/tools/ai-plan-cost": {
    title: "この計算ツールは終了しました",
    text: "「AIの有料プラン 費用計算」は終了しました。AIの料金と円の目安は早見表で確かめられます。",
    links: [
      ["/ai-pricing", "AIの料金・日本対応 早見表"],
      ["/products", "AIサービス図鑑"],
      ["/", "トップページ"],
    ],
  },
  "/tools/usd-jpy": {
    title: "この計算ツールは終了しました",
    text: "「ドル建てサブスクの円換算」は終了しました。主なAIサービスの料金は、公式の金額と円の目安を早見表と図鑑に載せています。",
    links: [
      ["/ai-pricing", "AIの料金・日本対応 早見表"],
      ["/products", "AIサービス図鑑"],
      ["/", "トップページ"],
    ],
  },
};

function retiredPageResponse(request: Request, url: URL): Response | null {
  const path = url.pathname.replace(/\.rsc$/, "").replace(/\/+$/, "");
  const page = RETIRED_PAGES[path];
  if (!page) return null;
  const links = page.links.map(([href, label]) => `<li><a href="${href}">${label}</a></li>`).join("");
  const html = GONE_HTML.replace(/<title>[^<]*<\/title>/, `<title>${page.title}｜サイソクニュース</title>`).replace(
    /<h1>[\s\S]*<\/ul>/,
    `<h1>${page.title}</h1><p>${page.text}</p><ul>${links}</ul>`,
  );
  const headers = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=3600", "X-Robots-Tag": "noindex" };
  return new Response(request.method === "HEAD" || request.headers.has("rsc") ? null : html, { status: 410, headers });
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

/**
 * 記事のカバー画像（/cover/<slug>?v=<版>-<題名のハッシュ>）。中身は URL だけで決まり、ブラウザにも7日 immutable で渡している。
 * デプロイのたびに作り直す必要は無いので、エッジのキャッシュはデプロイ・JS の組に関係なく使い、古い版としての作り直しもしない（台帳 W29）。
 * 以前は、デプロイのたびに画像の見回り（scripts/check_images.py）などが開くたびに1枚 1.5〜3.6 秒の CPU で作り直し、
 * 同じ isolate のページのリクエストを待たせていた（2026-11-04 01:13 UTC のデプロイ直後、/usable が CPU 1ms のまま 90 秒待って切れた）
 */
function isImmutableImage(url: URL): boolean {
  return url.pathname.startsWith("/cover/") && /^\d+-[0-9a-z]+$/.test(url.searchParams.get("v") ?? "");
}

function edgeCacheKey(request: Request, url: URL, env: Env): Request | null {
  if (request.method !== "GET" || request.headers.has("rsc") || url.searchParams.has("_rsc")) return null;
  if (url.pathname.startsWith("/_next/") || url.pathname.startsWith("/api/")) return null;
  if (isImmutableImage(url)) return new Request(`${url.origin}${url.pathname}?v=${url.searchParams.get("v")}&__c=immutable`, { method: "GET" });
  const key = new URL(url);
  // 表示に関係しない計測用のパラメータ（シェアボタンの utm_*・SNS のクリック ID）はキーから外し、
  // 同じページを別物として描き直さない。/search の q もページは同じ（検索はブラウザ側で行う）
  for (const name of [...key.searchParams.keys()]) {
    if (/^utm_/.test(name) || name === "fbclid" || name === "gclid" || (name === "q" && key.pathname === "/search")) key.searchParams.delete(name);
  }
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
function forEdge(response: Response, env: Env, immutable = false): Response {
  const copy = new Response(response.body, response);
  const image = (copy.headers.get("content-type") ?? "").startsWith("image/");
  copy.headers.set(
    "cache-control",
    immutable && image ? "public, max-age=604800, s-maxage=604800, immutable" : image ? "public, max-age=604800, s-maxage=86400" : "public, max-age=0, s-maxage=86400",
  );
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
    // 編集部の記事確認ページ（cloudflare/review.ts。ログインした編集部だけ。キャッシュしない）
    if (url.pathname === "/review" || url.pathname.startsWith("/review/")) return handleReview(request, url, env as unknown as ReviewEnv);

    // 取り下げた記事（301 か 410）・終了した固定ページ（410）・消えた製品ページ（親の製品へ 301）。エッジキャッシュに古い版の HTML が残っていても先に返す
    const retired = retiredPageResponse(request, url) ?? (await retiredResponse(request, url, env, ctx)) ?? (await retiredProductResponse(url, env));
    if (retired) return retired;

    const cacheKey = edgeCacheKey(request, url, env);
    const edge = cacheKey ? (caches as unknown as { default: Cache }).default : null;
    if (edge && cacheKey) {
      const hit = await edge.match(cacheKey);
      if (hit) {
        const fresh = isImmutableImage(url) || hit.headers.get("x-saisoku-ver") === deployVersion(env);
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
      const cached = forEdge(response, env, isImmutableImage(url));
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
