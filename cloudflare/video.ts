/**
 * SNS 用の解説動画の配信と、動画投稿から記事に来た人の数え方（cloudflare/worker.ts が使う）。
 * テスト: node scripts/test_video.mjs（本番の KV には通信しない）
 */
import { BOT_UA } from "../lib/revenue-funnel";

type Ctx = { waitUntil(promise: Promise<unknown>): void };
type VideoEnv = Record<string, unknown>;
type KvNamespace = { get(key: string, type: "arrayBuffer"): Promise<ArrayBuffer | null> };

const VIDEO_NAME = /^\/video\/([A-Za-z0-9][A-Za-z0-9_-]{0,160}\.(mp4|jpg))$/;

/**
 * SNS 用の解説動画（scripts/sns_video_followup.py が Workers KV の saisoku-images に「video/<slug>-<hash>.mp4」で置く。1本 25MiB 未満）。
 * 名前に中身のハッシュが入っていて変わらないので1年使い回す。エッジには全体（200）を置き、
 * Range（bytes=a-b）の要求にはそこから切り出して 206 で返す（動画プレーヤーや SNS の取り込みが Range を使うため）
 */
export async function serveVideo(request: Request, url: URL, env: VideoEnv, ctx: Ctx): Promise<Response> {
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
        "Content-Type": match[2] === "jpg" ? "image/jpeg" : "video/mp4",
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

/**
 * SNS の動画投稿（utm_campaign=sns_video・utm_content=short|full）から記事に来た人を数える（短い版と全部の版の比べ）。
 * AFF_KV に「v:<日付>:<slug>:<版>:<時分秒><乱数>」の空のキーを1つ置く（400日で消える）。ボット・プレビューは数えない。
 * scripts/sns_video_followup.py --ab-report が数える
 */
export function countVideoVisit(request: Request, url: URL, env: VideoEnv, ctx: Ctx): void {
  if (request.method !== "GET" || url.searchParams.get("utm_campaign") !== "sns_video") return;
  const variant = url.searchParams.get("utm_content");
  const m = /^\/articles\/([A-Za-z0-9][A-Za-z0-9_-]{0,160})\/?$/.exec(url.pathname);
  const kv = env.AFF_KV as { put(key: string, value: string, opts?: { expirationTtl?: number }): Promise<void> } | undefined;
  if (!m || !kv || (variant !== "short" && variant !== "full") || BOT_UA.test(request.headers.get("user-agent") || "")) return;
  const now = new Date(Date.now() + 9 * 3600_000).toISOString();
  const key = `v:${now.slice(0, 10)}:${m[1]}:${variant}:${now.slice(11, 19).replace(/:/g, "")}${Math.random().toString(36).slice(2, 8)}`;
  ctx.waitUntil(kv.put(key, "", { expirationTtl: 400 * 86400 }).catch((error) => console.error("[video] visit count failed", String(error))));
}

