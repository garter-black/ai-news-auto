/** /video/ の配信（Range・HEAD・416）と、動画投稿からの訪問の数え方のテスト。node scripts/test_video.mjs */
import assert from "node:assert/strict";
import { countVideoVisit, serveVideo } from "./video";

const cache = new Map<string, Response>();
Object.defineProperty(globalThis, "caches", { configurable: true, value: { default: {
  match: async (request: Request) => cache.get(request.url)?.clone(),
  put: async (request: Request, response: Response) => { cache.set(request.url, response.clone()); },
} } });
const bytes = new Uint8Array(1000).map((_, i) => i % 256);
const env = {
  IMAGES_KV: { get: async (key: string) => (key === "video/demo-abc.mp4" ? bytes.buffer.slice(0) : null) },
  AFF_KV: { put: async (key: string) => { visits.push(key); } },
};
const visits: string[] = [];
const waits: Promise<unknown>[] = [];
const ctx = { waitUntil: (p: Promise<unknown>) => { waits.push(p); } };
const req = (path: string, init: RequestInit = {}) => new Request(`https://ai-tech-express.jp${path}`, init);
const call = (path: string, init: RequestInit = {}) => serveVideo(req(path, init), new URL(req(path).url), env, ctx);

let res = await call("/video/demo-abc.mp4");
assert.equal(res.status, 200);
assert.equal(res.headers.get("content-type"), "video/mp4");
assert.equal(res.headers.get("accept-ranges"), "bytes");
assert.equal((await res.arrayBuffer()).byteLength, 1000);
await Promise.all(waits);

res = await call("/video/demo-abc.mp4", { headers: { Range: "bytes=100-199" } });
assert.equal(res.status, 206);
assert.equal(res.headers.get("content-range"), "bytes 100-199/1000");
const part = new Uint8Array(await res.arrayBuffer());
assert.equal(part.length, 100);
assert.equal(part[0], 100);

res = await call("/video/demo-abc.mp4", { headers: { Range: "bytes=900-" } });
assert.equal(res.headers.get("content-range"), "bytes 900-999/1000");
res = await call("/video/demo-abc.mp4", { headers: { Range: "bytes=-10" } });
assert.equal(res.headers.get("content-range"), "bytes 990-999/1000");
res = await call("/video/demo-abc.mp4", { headers: { Range: "bytes=5000-" } });
assert.equal(res.status, 416);
res = await call("/video/demo-abc.mp4", { method: "HEAD" });
assert.equal(res.status, 200);
assert.equal(res.headers.get("content-length"), "1000");
assert.equal((await call("/video/nothing-here.mp4")).status, 404);
assert.equal((await call("/video/../secret.mp4")).status, 404);

const visit = (path: string, ua = "Mozilla/5.0 (iPhone) Safari") => countVideoVisit(req(path, { headers: { "user-agent": ua } }), new URL(req(path).url), env, ctx);
visit("/articles/claude-ultra?utm_source=threads&utm_medium=social&utm_campaign=sns_video&utm_content=short");
visit("/articles/claude-ultra?utm_campaign=sns_video&utm_content=full", "facebookexternalhit/1.1 preview");
visit("/articles/claude-ultra?utm_campaign=other&utm_content=full");
visit("/articles/claude-ultra?utm_campaign=sns_video&utm_content=long");
await Promise.all(waits);
assert.equal(visits.length, 1);
assert.match(visits[0], /^v:\d{4}-\d{2}-\d{2}:claude-ultra:short:\d{6}[a-z0-9]+$/);
console.log("video tests ok");
