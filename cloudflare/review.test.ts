/** 編集部の記事確認ページ（cloudflare/review.ts）の回帰テスト。本番の KV・メールには通信しない。node scripts/test_aff.mjs */
import assert from "node:assert/strict";
import { handleReview } from "./review";

const records = new Map<string, { value: string; metadata?: unknown }>();
const kv = {
  get: async (key: string) => records.get(key)?.value ?? null,
  delete: async (key: string) => { records.delete(key); },
  put: async (key: string, value: string, options?: { metadata?: unknown }) => { records.set(key, { value, metadata: options?.metadata }); },
  list: async ({ prefix = "" }: { prefix?: string }) => ({ keys: [...records].filter(([k]) => k.startsWith(prefix)).map(([name, r]) => ({ name, metadata: r.metadata })), list_complete: true }),
};
const sent: { to: string[]; subject: string; text: string }[] = [];
const realFetch = globalThis.fetch;
globalThis.fetch = (async (input: string | Request, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input.url;
  assert.equal(url, "https://api.resend.com/emails");
  sent.push(JSON.parse(String(init?.body)));
  return new Response("{}", { status: 200 });
}) as typeof fetch;
const assets = { fetch: async () => Response.json({ articles: [{ slug: "test-article", title: "テスト記事", genre: "AI", generatedAt: "2026-11-03T00:00:00Z", breaking: true }] }) };
const env = { REVIEW_KV: kv, REVIEW_OWNER_EMAIL: "owner@test.invalid", RESEND_API_KEY: "test-only", ASSETS: assets };
const ORIGIN = "https://site.test";
let cookie = "";
async function call(path: string, body?: unknown, opts: { method?: string; headers?: Record<string, string> } = {}) {
  const method = opts.method ?? (body === undefined ? "GET" : "POST");
  const headers: Record<string, string> = { ...(method === "POST" ? { origin: ORIGIN, "x-saisoku-review": "1", "content-type": "application/json" } : {}), ...(cookie ? { cookie } : {}), ...opts.headers };
  const request = new Request(`${ORIGIN}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  return handleReview(request, new URL(request.url), env);
}

try {
  // ログイン前: コードを受け取る画面。キャッシュしない・検索に出さない
  let res = await call("/review");
  assert.equal(res.status, 200);
  assert.match(await res.text(), /確認コードをメールで受け取る/);
  assert.match(res.headers.get("cache-control") ?? "", /no-store/);
  assert.match(res.headers.get("x-robots-tag") ?? "", /noindex/);
  assert.match(res.headers.get("content-security-policy") ?? "", /frame-ancestors 'none'/);

  // ほかのサイトからの POST・合図のない POST は受けない
  assert.equal((await call("/review/api/code", {}, { headers: { origin: "https://evil.test" } })).status, 403);
  assert.equal((await call("/review/api/code", {}, { headers: { "x-saisoku-review": "" } })).status, 403);
  // ログイン前は一覧も記録もできない
  assert.equal((await call("/review/api/list")).status, 401);
  assert.equal((await call("/review/api/mark", { slug: "test-article", status: "ok" })).status, 401);

  // コードは決めた宛先にだけ送る。続けて押すと断る
  res = await call("/review/api/code", {});
  assert.equal(res.status, 200);
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].to, ["owner@test.invalid"]);
  const code = /(\d{6})/.exec(sent[0].text)![1];
  assert.equal((await call("/review/api/code", {})).status, 429);
  assert.ok(![...records.values()].some((r) => r.value.includes(code)), "コードそのものは保存しない");

  // 違うコードは断る（回数を数える）。正しいコードでログイン
  res = await call("/review/api/login", { code: code === "000000" ? "111111" : "000000" });
  assert.equal(res.status, 400);
  res = await call("/review/api/login", { code });
  assert.equal(res.status, 200);
  const setCookie = res.headers.get("set-cookie") ?? "";
  assert.match(setCookie, /^__Host-saisoku_review=[0-9a-f]{64}; Path=\/; Secure; HttpOnly; SameSite=Lax/);
  cookie = setCookie.split(";")[0];
  // 同じコードは2回使えない
  assert.equal((await call("/review/api/login", { code })).status, 400);

  // 一覧と記録
  res = await call("/review");
  assert.match(await res.text(), /確認済み/);
  res = await call("/review/api/list");
  assert.equal(res.status, 200);
  assert.equal(((await res.json()) as { articles: unknown[] }).articles.length, 1);
  assert.equal((await call("/review/api/mark", { slug: "../etc", status: "ok" })).status, 400);
  assert.equal((await call("/review/api/mark", { slug: "not-listed", status: "ok" })).status, 404);
  assert.equal((await call("/review/api/mark", { slug: "test-article", status: "approve" })).status, 400);
  res = await call("/review/api/mark", { slug: "test-article", status: "fix", note: "見出しの数字が違う\u0007" });
  assert.equal(res.status, 200);
  let saved = JSON.parse(records.get("r:test-article")!.value);
  assert.equal(saved.status, "fix");
  assert.equal(saved.note, "見出しの数字が違う");
  assert.deepEqual(Object.keys(records.get("r:test-article")!.metadata as object).sort(), ["at", "status"]);
  res = await call("/review/api/mark", { slug: "test-article", status: "ok" });
  saved = JSON.parse(records.get("r:test-article")!.value);
  assert.equal(saved.status, "ok");
  res = await call("/review/api/mark", { slug: "test-article", status: "clear" });
  assert.equal((records.get("r:test-article")!.metadata as { status: string }).status, "clear");

  // ログアウトすると使えない
  res = await call("/review/api/logout", {});
  assert.equal(res.status, 200);
  assert.equal((await call("/review/api/list")).status, 401);
  console.log("review: ok");
} finally {
  globalThis.fetch = realFetch;
}
