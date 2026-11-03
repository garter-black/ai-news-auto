/** 記事への流入計測（lib/inflow.ts の分類・cloudflare/inflow.ts の記録と集計）の回帰テスト。本番 KV には通信しない。node scripts/test_aff.mjs */
import assert from "node:assert/strict";
import { handleAff } from "./aff";
import { classifyInflow, pageKey, sourceFromReferrer, INFLOW_BOT_UA } from "../lib/inflow";

const O = "https://ai-tech-express.jp";
const records = new Map<string, { value: string; metadata?: unknown; expirationTtl?: number }>();
const cache = new Map<string, { response: Response; until: number }>();
Object.defineProperty(globalThis, "caches", { configurable: true, value: { default: {
  match: async (request: Request) => {
    const item = cache.get(request.url);
    return item && item.until > Date.now() ? item.response.clone() : undefined;
  },
  put: async (request: Request, response: Response) => {
    cache.set(request.url, { response: response.clone(), until: Date.now() + Number(response.headers.get("cache-control")?.split("=")[1]) * 1000 });
  },
} } });
const kv = {
  get: async (key: string) => records.get(key)?.value ?? null,
  delete: async (key: string) => { records.delete(key); },
  put: async (key: string, value: string, options?: { metadata?: unknown; expirationTtl?: number }) => { records.set(key, { value, ...options }); },
  list: async ({ prefix = "", cursor = "0" }: { prefix?: string; cursor?: string }) => {
    const keys = [...records].filter(([key]) => key.startsWith(prefix)).map(([name, item]) => ({ name, metadata: item.metadata }));
    const start = Number(cursor), end = start + 3;
    return { keys: keys.slice(start, end), list_complete: end >= keys.length, cursor: String(end) };
  },
};
const env = { AFF_KV: kv, AFF_STATS_TOKEN: "test-only-not-a-production-token" };
const IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Barcelona 350.0";
let ip = 0;
async function view(body: Record<string, string>, headers: Record<string, string> = {}) {
  const req = new Request(`${O}/api/aff/view`, { method: "POST", body: JSON.stringify(body), headers: {
    origin: O, "sec-fetch-site": "same-origin", "user-agent": IPHONE, "cf-connecting-ip": `198.51.100.${++ip}`, ...headers,
  } });
  const pending: Promise<unknown>[] = [];
  const res = await handleAff(req, new URL(req.url), env, { waitUntil: (p) => pending.push(p) });
  await Promise.all(pending);
  return res;
}
async function inflow(date: string, token = env.AFF_STATS_TOKEN) {
  const req = new Request(`${O}/api/aff/inflow?date=${date}`, { headers: { authorization: `Bearer ${token}` } });
  return handleAff(req, new URL(req.url), env, { waitUntil: () => {} });
}
const keys = (prefix: string) => [...records.keys()].filter((key) => key.startsWith(prefix));
let passed = 0;
async function test(name: string, run: () => void | Promise<void>) { records.clear(); cache.clear(); await run(); passed++; console.log(`ok inflow ${name}`); }

await test("ページの名前", () => {
  assert.equal(pageKey("/articles/claude-ultra"), "claude-ultra");
  assert.equal(pageKey("/articles/claude-ultra/"), "claude-ultra");
  assert.equal(pageKey("/digest/20261103"), "digest-20261103");
  assert.equal(pageKey("/"), "_home");
  assert.equal(pageKey("/news"), null);
  assert.equal(pageKey("/articles/../x"), null);
});

await test("流入元の分類（utm を優先・参照元のホスト）", () => {
  assert.deepEqual(classifyInflow("?utm_source=threads&utm_medium=social&utm_campaign=classic", "", O), { source: "threads", campaign: "classic", content: "" });
  assert.deepEqual(classifyInflow("?utm_source=threads&utm_campaign=sns_video&utm_content=short", "https://l.threads.com/", O), { source: "threads", campaign: "sns_video", content: "short" });
  assert.equal(classifyInflow("?utm_source=twitter", "", O)?.source, "x");
  assert.equal(classifyInflow("?utm_source=Bluesky", "", O)?.source, "bluesky");
  assert.equal(classifyInflow("?utm_source=evil<script>", "", O)?.source, "direct");
  assert.equal(classifyInflow("?utm_source=threads&_internal=1", "", O), null);
  assert.equal(classifyInflow("", `${O}/news`, O), null);
  const ref = (url: string) => sourceFromReferrer(url, O);
  assert.equal(ref("https://l.threads.com/"), "threads");
  assert.equal(ref("https://www.threads.net/@x"), "threads");
  assert.equal(ref("https://t.co/abc"), "x");
  assert.equal(ref("https://lm.facebook.com/"), "facebook");
  assert.equal(ref("https://bsky.app/profile/x"), "bluesky");
  assert.equal(ref("https://www.google.co.jp/"), "google");
  assert.equal(ref("https://news.google.com/"), "news");
  assert.equal(ref("android-app://com.google.android.googlequicksearchbox/"), "discover");
  assert.equal(ref("https://search.yahoo.co.jp/search?p=x"), "yahoo");
  assert.equal(ref("https://news.yahoo.co.jp/"), "news");
  assert.equal(ref("https://www.bing.com/"), "bing");
  assert.equal(ref("https://www.smartnews.com/"), "news");
  assert.equal(ref(""), "direct");
  assert.equal(ref("https://example.com/"), "other");
});

await test("機械・プレビュー・計測ツールの UA", () => {
  for (const ua of [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; moto g power (2022)) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36 Chrome-Lighthouse",
    "Mozilla/5.0 (compatible; Google-PageSpeed Insights)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "meta-externalagent/1.1", "Twitterbot/1.0", "Bluesky Cardyb/1.1", "Slackbot-LinkExpanding 1.0", "curl/8.7.1", "python-requests/2.32",
  ]) assert.ok(INFLOW_BOT_UA.test(ua), ua);
  assert.ok(!INFLOW_BOT_UA.test(IPHONE));
  assert.ok(!INFLOW_BOT_UA.test("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"));
});

await test("SNS ごとに記録し、自社テスト・機械・不正な値は数えない", async () => {
  assert.equal((await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" })).status, 204);
  assert.equal((await view({ p: "claude-ultra", s: "threads", c: "sns_video", t: "short" })).status, 204);
  assert.equal((await view({ p: "claude-ultra", s: "threads", c: "sns_video", t: "full" })).status, 204);
  assert.equal((await view({ p: "claude-ultra", s: "x", c: "classic", t: "" })).status, 204);
  assert.equal((await view({ p: "claude-ultra", s: "facebook", c: "classic", t: "" })).status, 204);
  assert.equal((await view({ p: "claude-ultra", s: "bluesky", c: "fill", t: "" })).status, 204);
  assert.equal((await view({ p: "gemini-4", s: "google", c: "", t: "" })).status, 204);
  assert.equal((await view({ p: "digest-20261103", s: "x", c: "digest", t: "" })).status, 204);
  assert.equal(keys("i:").length, 8);
  assert.equal(keys("v:").length, 2);
  assert.ok(keys("v:").some((key) => key.includes(":claude-ultra:short:")));
  // 数えないもの
  await view({ p: "claude-ultra", s: "direct", c: "", t: "" }, { "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/131.0.0.0 Safari/537.36" });
  await view({ p: "claude-ultra", s: "direct", c: "", t: "" }, { "user-agent": "Mozilla/5.0 Chrome/131.0.0.0 Mobile Safari/537.36 Chrome-Lighthouse" });
  await view({ p: "claude-ultra", s: "direct", c: "", t: "" }, { "x-saisoku-internal": "1" });
  await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" }, { referer: `${O}/articles/claude-ultra?utm_source=threads&_internal=1` });
  await view({ p: "claude-ultra", s: "direct", c: "", t: "" }, { "user-agent": "" });
  assert.equal(keys("i:").length, 8);
  assert.equal((await view({ p: "claude-ultra", s: "direct", c: "", t: "" }, { origin: "https://evil.example" })).status, 403);
  assert.equal((await view({ p: "../x", s: "direct", c: "", t: "" })).status, 400);
  assert.equal((await view({ p: "claude-ultra", s: "myspace", c: "", t: "" })).status, 400);
  assert.equal((await view({ p: "claude-ultra", s: "x", c: "<b>", t: "" })).status, 400);
  assert.equal(keys("i:").length, 8);
});

await test("同じ人の開き直しは30分に1回・訪問者のハッシュだけ残す", async () => {
  const same = { "cf-connecting-ip": "203.0.113.9" };
  await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" }, same);
  await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" }, same);
  await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" }, { ...same, dnt: "1" });
  assert.equal(keys("i:").length, 1);
  const meta = records.get(keys("i:")[0])!.metadata as Record<string, string>;
  assert.deepEqual(Object.keys(meta).sort(), ["c", "p", "s", "v"]);
  assert.match(meta.v, /^[0-9a-f]{12}$/);
  assert.ok(!JSON.stringify(meta).includes("203.0.113.9"));
  await view({ p: "other-article", s: "threads", c: "classic", t: "" }, { "cf-connecting-ip": "203.0.113.10", dnt: "1" });
  assert.equal((records.get(keys("i:").find((key) => (records.get(key)!.metadata as { p: string }).p === "other-article")!)!.metadata as Record<string, string>).v, undefined);
});

await test("集計 API（鍵が必要・流入元別・記事 × utm）", async () => {
  await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" });
  await view({ p: "claude-ultra", s: "threads", c: "classic", t: "" });
  await view({ p: "claude-ultra", s: "threads", c: "sns_video", t: "short" });
  await view({ p: "gemini-4", s: "google", c: "", t: "" });
  const date = keys("i:")[0].slice(2, 12);
  assert.equal((await inflow(date, "wrong-token-wrong-token")).status, 401);
  const data = await (await inflow(date)).json() as { views: number; visitors: number; bySource: { source: string; views: number }[]; rows: { page: string; source: string; campaign: string; content: string; views: number }[] };
  assert.equal(data.views, 4);
  assert.equal(data.visitors, 4);
  assert.deepEqual(data.bySource.map((row) => [row.source, row.views]), [["threads", 3], ["google", 1]]);
  assert.deepEqual(data.rows.find((row) => row.page === "claude-ultra" && row.campaign === "classic")?.views, 2);
  assert.deepEqual(data.rows.find((row) => row.content === "short")?.views, 1);
  // 締まった日は集計を1回だけ保存して次から使う
  const past = "2026-01-02";
  records.set(`i:${past}:101010abc`, { value: "", metadata: { p: "claude-ultra", s: "bluesky", v: "aaaaaaaaaaaa" } });
  const first = await inflow(past);
  assert.equal(first.headers.get("x-inflow-summary"), null);
  assert.ok(records.has(`is:${past}`));
  const second = await inflow(past);
  assert.equal(second.headers.get("x-inflow-summary"), "saved");
  assert.equal(((await second.json()) as { views: number }).views, 1);
});

console.log(`inflow tests ok (${passed})`);
