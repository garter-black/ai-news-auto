// レーダーのテスト（node --test test/）。2026-10-26〜27 に取りこぼした4本（Anthropic の S-1・Genie 4・Mithril の買収・
// Siri AI の日本語対応）の実際の見出し（fixtures/radar）で、レーダーなら拾えていたことを確かめる
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { compile, fastLane, itemId, takeRadar } from "../src/fastlane.js";
import { classify, compileRadar, covered, parseTechmeme, prepare, radar, resolvePrimary } from "../src/radar.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const RULES = JSON.parse(readFileSync(join(ROOT, "scripts", "fast_lane_rules.json"), "utf-8"));
const RR = compileRadar(RULES);
const fx = (name) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), "fixtures", "radar", name), "utf-8");
const PAGES = JSON.parse(fx("pages.json"));
const BEFORE = JSON.parse(fx("coverage_before.json")).items;

// 取りこぼした4本の記事ができたあとの索引（記事の題・元記事の題・出典は実際のものに近づけた架空の記録）
const AFTER = [
  { slug: "anthropic-ipo-s1-filing", at: "2026-10-28T01:00:00Z", title: "Anthropic、米SECにS-1を提出しNasdaq上場へ 銘柄コードはANT",
    head: "Anthropic、米SECにS-1を提出しNasdaq上場へ 銘柄コードはANT | Anthropic files S-1 with the SEC for proposed initial public offering",
    text: "Anthropic、米SECにS-1を提出しNasdaq上場へ 銘柄コードはANT | Anthropic files S-1 with the SEC for proposed initial public offering | Anthropic / IPO / S-1",
    urls: ["https://www.anthropic.com/news/s1-sec"] },
  { slug: "google-deepmind-genie-4-world-model", at: "2026-10-28T01:05:00Z", title: "Google DeepMind、世界モデル「Genie 4」を発表 操作できる仮想世界をリアルタイム生成",
    head: "Google DeepMind、世界モデル「Genie 4」を発表 操作できる仮想世界をリアルタイム生成 | We’re announcing Genie 4, our new world model for real-time interactive simulation.",
    text: "Google DeepMind、世界モデル「Genie 4」を発表 | Genie 4 / Google DeepMind / 世界モデル",
    urls: ["https://blog.google/innovation-and-ai/models-and-research/google-deepmind/introducing-genie-4/"] },
  { slug: "siri-ai-japanese-ios-27-2", at: "2026-10-28T02:00:00Z", title: "Siri AIが日本語に対応、iOS 27.2で提供開始",
    head: "Siri AIが日本語に対応、iOS 27.2で提供開始 | Siri AI、日本語で提供開始", text: "Siri AIが日本語に対応、iOS 27.2で提供開始 | Siri AI / iOS 27.2 / Apple",
    urls: ["https://www.apple.com/jp/newsroom/2026/10/siri-ai-is-available-today-in-more-languages/"] },
  { slug: "anthropic-drug-development-mithril", at: "2026-10-28T02:10:00Z", title: "Anthropic、Claudeで創薬を加速 筋ジストロフィーのMithrilを買収",
    head: "Anthropic、Claudeで創薬を加速 筋ジストロフィーのMithrilを買収 | Early progress on accelerating drug development with Claude",
    text: "Anthropic、Claudeで創薬を加速 筋ジストロフィーのMithrilを買収 | Anthropic / Mithril / 創薬", urls: ["https://www.anthropic.com/research/early-progress-drug-development"] },
];

// 取りこぼした4本の見出し（どの情報源でも拾えること）
const MISSED = {
  s1: "Anthropic files for an IPO on the Nasdaq under the symbol ANT; the number of Class A shares to be offered and the price range have not yet been determined",
  s1ja: "Anthropic、IPOを正式申請　上半期売上高は約14倍、純損失807億ドル",
  genie: "Google DeepMind launches Genie 4, a world model powered by Gemini 3.8 Flash designed to generate interactive AI simulations, in preview first to trusted testers",
  genieja: "Google DeepMind、ワールドモデル「Genie 4」発表　音声や会話もリアルタイム生成、Geminiと連携",
  mithril: "Anthropic details early drug development work with six partners and says it has acquired Mithril, a one-person company working on muscular dystrophy",
  siri: "「iOS 27.2」「iPadOS 27.2」が提供開始、「Siri AI」の日本語対応や100以上の修正",
  siri2: "AppleのSiri AIが日本語でも利用可能に。画面認識、文章作成などをアシスト",
  siri3: "Apple debuts iOS 27.2, with Siri AI enhancements for recalling past conversations, understanding more personal context, and more",
};
const NOT_CANDIDATES = [
  "Source: OpenAI, a major Nvidia customer, plans to buy Google's TPUs; a job listing shows it seeks an engineer for TPU work",
  "Sources: Anthropic is aiming to go public the week of November 16 and could raise ~$100B",
  "SK Hynix reports Q3 revenue up 328% YoY to ~$78.8B, vs. ~$74.07B est., and operating profit up 632%",
  "「Pixel 10a」で使えるおすすめの設定5選",
  "ドコモ、「Galaxy S27」の予約を開始",
  "Anthropic expands its US GSA partnership to give governments a 40% discount",
  "Infisical, which helps companies use AI agents without handing over their private credentials, raises $40M",
];

function python(args, input) {
  const r = spawnSync("python3", ["scripts/radar.py", ...args], { input: JSON.stringify(input), cwd: ROOT, encoding: "utf-8" });
  assert.equal(r.status, 0, r.stderr);
  return JSON.parse(r.stdout);
}

// --------------------------------------------------------------------------- //
// 判定
// --------------------------------------------------------------------------- //

test("取りこぼした4本の見出しはすべて候補になる・噂や決算や使い方の記事はならない", () => {
  for (const [k, title] of Object.entries(MISSED)) assert.equal(classify(title, RR).ok, true, `${k}: ${title}`);
  for (const title of NOT_CANDIDATES) assert.equal(classify(title, RR).ok, false, title);
});

test("判定が Python（scripts/radar.py classify）と同じ", () => {
  const titles = [...Object.values(MISSED), ...NOT_CANDIDATES];
  assert.deepEqual(titles.map((t) => classify(t, RR)), python(["classify"], titles.map((title) => ({ title }))));
});

test("当時の記事（取りこぼした4本が無い）では「記事あり」にしない（iMac の記事のキーワードの Siri AI では当たらない）", () => {
  const records = prepare(BEFORE, RR);
  for (const [k, title] of Object.entries(MISSED)) assert.equal(covered(title, [], records, RR), null, k);
});

test("記事ができたあとは、どの情報源の見出しでも「記事あり」になる（同じ話題を二度起動しない）", () => {
  const records = prepare([...BEFORE, ...AFTER], RR);
  const expect = {
    s1: "anthropic-ipo-s1-filing", s1ja: "anthropic-ipo-s1-filing", genie: "google-deepmind-genie-4-world-model",
    genieja: "google-deepmind-genie-4-world-model", mithril: "anthropic-drug-development-mithril", siri: "siri-ai-japanese-ios-27-2",
    siri2: "siri-ai-japanese-ios-27-2", siri3: "siri-ai-japanese-ios-27-2",
  };
  for (const [k, title] of Object.entries(MISSED)) assert.equal(covered(title, [], records, RR)?.slug, expect[k], k);
  // 出典の URL が同じなら見出しが違っても「記事あり」
  assert.equal(covered("Anything", ["https://anthropic.com/news/s1-sec/"], records, RR)?.reason, "同じ出典URL");
});

test("「記事あり」の判定が Python（scripts/radar.py covered）と同じ", () => {
  const cases = [...Object.values(MISSED), ...NOT_CANDIDATES].map((title) => ({ title, urls: [] }));
  for (const records of [BEFORE, [...BEFORE, ...AFTER]]) {
    const prepared = prepare(records, RR);
    const mine = cases.map((c) => covered(c.title, c.urls, prepared, RR)?.slug ?? null);
    assert.deepEqual(mine, python(["covered"], { records, cases }));
  }
});

// --------------------------------------------------------------------------- //
// 一次情報を探す
// --------------------------------------------------------------------------- //

const pageFetch = async (url) => (url in PAGES ? new Response(PAGES[url], { status: 200 }) : new Response("nf", { status: 404 }));
const cand = (title, links) => ({ title, links, entities: classify(title, RR).entities });

test("Mithril: Techmeme の見出しのリンクが公式（anthropic.com/research）なのでそのまま使う", async () => {
  const items = parseTechmeme(fx("techmeme_1027.xml"));
  const m = items.find((i) => /Mithril/.test(i.title));
  assert.equal(m.title, MISSED.mithril); // 末尾の「(Anthropic)」は外す
  const p = await resolvePrimary([{ ...m, entities: ["anthropic"] }], RR, pageFetch);
  assert.deepEqual([p.url, p.secondary, p.source], ["https://www.anthropic.com/research/early-progress-drug-development", false, "anthropic-news"]);
});

test("S-1: 日本の報道のページから公式の発表を探す（SEC の書類より会社の発表を優先）", async () => {
  const p = await resolvePrimary([cand(MISSED.s1ja, ["https://www.itmedia.co.jp/news/article/2610/27/2000002603/"])], RR, pageFetch);
  assert.deepEqual([p.url, p.sourceName, p.secondary], ["https://www.anthropic.com/news/s1-sec", "Anthropic公式", false]);
});

test("Genie 4: The Verge の記事から DeepMind の公式ブログを見つける", async () => {
  const p = await resolvePrimary([cand(MISSED.genie, ["https://www.theverge.com/tech/1019076/google-genie-4-ai-model"])], RR, pageFetch);
  assert.equal(p.url, "https://deepmind.google/blog/genie-4-a-new-generation-of-world-simulation/");
  assert.equal(p.source, "google-deepmind-blog");
});

test("Siri AI: ケータイ Watch に公式リンクが無くても、同じ話題の PC Watch から Apple の日本のニュースリリースを見つける", async () => {
  const group = [
    cand(MISSED.siri3, ["https://www.macrumors.com/2026/10/26/apple-releases-ios-27-2/"]),
    cand(MISSED.siri2, ["https://pc.watch.impress.co.jp/docs/news/2151546.html"]),
    cand(MISSED.siri, ["https://k-tai.watch.impress.co.jp/docs/news/2151489.html"]),
  ];
  const p = await resolvePrimary(group, RR, pageFetch);
  assert.equal(p.url, "https://www.apple.com/jp/newsroom/2026/10/siri-ai-is-available-today-in-more-languages/");
  assert.equal(p.secondary, false);
});

test("日本だけの話題で公式の発表が見つからなければ、信頼できる報道（ケータイ Watch）を出典にする", async () => {
  const p = await resolvePrimary([cand(MISSED.siri, ["https://k-tai.watch.impress.co.jp/docs/news/2151489.html"])], RR, pageFetch);
  assert.deepEqual([p.url, p.sourceName, p.secondary], ["https://k-tai.watch.impress.co.jp/docs/news/2151489.html", "ケータイ Watch", true]);
});

test("配信サービス（PR Wire 等）は出典にしない", async () => {
  const p = await resolvePrimary([cand("OpenAI announces GPT-9", ["https://www.prnewswire.com/news-releases/openai-gpt-9.html"])], RR, pageFetch);
  assert.equal(p.url, "");
});

// --------------------------------------------------------------------------- //
// 1回分の流れ（KV・情報源・GitHub は偽物）
// --------------------------------------------------------------------------- //

const EMPTY_RSS = '<?xml version="1.0"?><rss><channel><title>t</title></channel></rss>';

function harness() {
  const store = new Map();
  const world = { feeds: {}, coverage: [], requests: null, notified: [] };
  const env = {
    REPO: "o/r",
    FAST: {
      async get(k, type) {
        const v = store.get(k);
        return v == null ? null : type === "json" ? JSON.parse(v) : v;
      },
      async put(k, v) {
        store.set(k, v);
      },
    },
  };
  const fetchImpl = async (url) => {
    if (url.startsWith("https://raw.githubusercontent.com/o/r/main/state/coverage.json")) return Response.json({ items: world.coverage });
    if (url.startsWith("https://raw.githubusercontent.com/o/r/main/state/radar_requests.json")) {
      return world.requests ? Response.json({ items: world.requests }) : new Response("nf", { status: 404 });
    }
    if (url === "https://hacker-news.firebaseio.com/v0/topstories.json") return Response.json([]);
    if (url in world.feeds) return new Response(world.feeds[url], { status: 200 });
    if (RR.sources.some((s) => s.url === url)) return new Response(EMPTY_RSS, { status: 200 });
    return pageFetch(url);
  };
  const notify = async (key, title, body) => {
    world.notified.push({ key, title, body });
    return true;
  };
  const run = (now, extra = {}) => radar({ ...env, ...extra }, new Date(now), { rules: RULES, fetch: fetchImpl, notify });
  const outbox = () => Object.values(JSON.parse(store.get("radar")).outbox);
  return { store, world, env, run, outbox, state: () => JSON.parse(store.get("radar")) };
}

const TECHMEME = "https://www.techmeme.com/feed.xml";
const ITMEDIA = "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml";
const KTAI = "https://k-tai.watch.impress.co.jp/data/rss/1.0/ktw/feed.rdf";
const PCW = "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf";

test("初回は印を付けるだけで起動しない（デプロイ直後に昔の話題を流さない）", async () => {
  const h = harness();
  h.world.feeds[TECHMEME] = fx("techmeme_1027.xml");
  const log = await h.run("2026-10-28T03:00:00Z");
  assert.ok(log.baseline);
  assert.equal(h.outbox().length, 0);
});

test("10/26〜27 を再現: S-1 → Siri AI → Genie 4 → Mithril の順にすべて一次情報で速報レーンに回り、同じ話題は二度回さない", async () => {
  const h = harness();
  h.world.coverage = BEFORE;
  await h.run("2026-10-26T12:00:00Z"); // 初回（印だけ）

  // 10/26 13:41Z: Anthropic の S-1（公式の発表へのリンク）。The Verge の S-1 の解説（「may」）・WSJ の観測（「Sources:」）は候補にしない
  h.world.feeds[TECHMEME] = fx("techmeme_1026.xml");
  let log = await h.run("2026-10-26T13:41:00Z");
  assert.deepEqual(log.dispatched.map((d) => d.url), ["https://www.anthropic.com/news/s1-sec"]);
  assert.equal(log.dispatched[0].sourceName, "Anthropic公式");

  // 10/27 07:21Z: ITmedia の IPO 申請（S-1 と同じ話題）・MacRumors / ケータイ Watch / PC Watch の iOS 27.2 と Siri AI
  h.world.feeds[ITMEDIA] = fx("itmedia.xml");
  h.world.feeds[KTAI] = fx("ktai.rdf");
  h.world.feeds[PCW] = fx("pcw.rdf");
  log = await h.run("2026-10-27T07:21:00Z");
  assert.deepEqual(log.dispatched.map((d) => d.url), ["https://www.apple.com/jp/newsroom/2026/10/siri-ai-is-available-today-in-more-languages/"]);
  assert.equal(log.dispatched[0].also, 2);
  assert.ok(log.covered.some((c) => c.startsWith("Anthropic、IPOを正式申請")), log.covered.join("\n"));

  // 10/27 16:13Z: Genie 4（The Verge の記事 → DeepMind の公式ブログ）
  log = await h.run("2026-10-27T16:13:00Z");
  assert.deepEqual(log.dispatched.map((d) => d.url), ["https://deepmind.google/blog/genie-4-a-new-generation-of-world-simulation/"]);

  // 10/27 21:55Z: Mithril の買収（公式の研究ブログ）。噂（Source:）と決算は回さない。
  // ITmedia の Genie 4（日本語の見出し・21:53Z）は、起動済みの Genie 4 と同じ話題
  h.world.feeds[TECHMEME] = fx("techmeme_1027.xml");
  log = await h.run("2026-10-27T21:55:00Z");
  assert.deepEqual(log.dispatched.map((d) => d.url), ["https://www.anthropic.com/research/early-progress-drug-development"]);
  assert.ok(log.covered.some((c) => c.startsWith("Google DeepMind、ワールドモデル")), log.covered.join("\n"));

  // 次の回は何もしない（扱った見出しは KV に残っている）
  log = await h.run("2026-10-27T21:59:00Z");
  assert.equal(log.dispatched.length + log.covered.length + log.candidates.length, 0);

  const out = h.outbox();
  assert.equal(out.length, 4);
  assert.ok(out.every((e) => !e.secondary));
  assert.deepEqual(h.state().daily, { "2026-10-26": 1, "2026-10-27": 1, "2026-10-28": 2 }); // 日本時間の日付で数える
});

test("すでに記事がある話題は回さない", async () => {
  const h = harness();
  h.world.coverage = [...BEFORE, ...AFTER];
  await h.run("2026-10-27T00:00:00Z");
  h.world.feeds[TECHMEME] = fx("techmeme_1027.xml");
  h.world.feeds[KTAI] = fx("ktai.rdf");
  const log = await h.run("2026-10-27T21:49:00Z");
  assert.equal(log.dispatched.length, 0);
  assert.equal(log.covered.length, 2);
});

test("見逃しチェック・手動の依頼（state/radar_requests.json）でも、報道機関の記事が出典なら回さない（台帳 radar-no-outlet）", async () => {
  const h = harness();
  await h.run("2026-10-28T00:00:00Z");
  h.world.requests = [
    { url: "https://endpoints.news/anthropic-buys-a-single-person-biotech-startup-backed-by-atlas-venture/", title: "Anthropic buys a single-person biotech startup",
      sourceName: "Endpoints News", secondary: true, requestedAt: "2026-10-28T00:30:00Z", via: "manual" },
    { url: "https://www.prnewswire.com/news-releases/x.html", title: "PR", requestedAt: "2026-10-28T00:30:00Z" },
  ];
  const log = await h.run("2026-10-28T00:33:00Z");
  assert.equal(log.dispatched.length, 0);
  assert.ok(log.noPrimary.some((n) => n.includes("報道のみ: Endpoints News")), log.noPrimary.join("\n"));
  assert.equal(h.outbox().length, 0);
  assert.equal((await h.run("2026-10-28T00:37:00Z")).dispatched.length, 0);
});

test("新たに許可した公式URLへの拒否済み依頼だけを再判定し、重複起動しない", async () => {
  const h = harness();
  await h.run("2026-10-28T00:00:00Z");
  const url = "https://www.figma.com/blog/the-tldr-on-figma-weave/";
  const id = `req:${await itemId(url)}`;
  const st = h.state();
  st.handled[id] = { status: "rejected", reason: "対象外のURL", at: "2026-10-28T00:01:00Z" };
  h.store.set("radar", JSON.stringify(st));
  h.world.requests = [{ url, title: "Figma Weave AI generation workflows", sourceName: "Figma公式ブログ", secondary: false, requestedAt: "2026-10-28T00:30:00Z", via: "manual" }];
  const log = await h.run("2026-10-28T00:33:00Z");
  assert.equal(log.dispatched.length, 1);
  assert.equal(log.dispatched[0].secondary, false);
  assert.equal((await h.run("2026-10-28T00:37:00Z")).dispatched.length, 0);
});

test("レーダーの1日の安全上限に達したら回さず、Issue で知らせる（1日1回）", async () => {
  const h = harness();
  h.world.coverage = BEFORE;
  await h.run("2026-10-27T00:00:00Z");
  h.world.feeds[TECHMEME] = fx("techmeme_1027.xml");
  const log = await h.run("2026-10-27T21:49:00Z", { RADAR_DAILY_MAX: "0" });
  assert.equal(log.dispatched.length, 0);
  assert.equal(log.capped.length, 1);
  assert.equal(h.world.notified.length, 1);
  assert.match(h.world.notified[0].title, /上限/);
});

test("RADAR_DRY_RUN=1 なら outbox に置かずログだけ", async () => {
  const h = harness();
  await h.run("2026-10-27T00:00:00Z");
  h.world.feeds[TECHMEME] = fx("techmeme_1027.xml");
  const log = await h.run("2026-10-27T21:49:00Z", { RADAR_DRY_RUN: "1" });
  assert.equal(log.dispatched.length, 1);
  assert.equal(h.outbox().length, 0);
});

// --------------------------------------------------------------------------- //
// 速報レーンへの受け渡し
// --------------------------------------------------------------------------- //

test("速報レーンが outbox を取り込み、出典の名前・報道かどうかを添えて起動する（1回だけ取り込む）", async () => {
  const store = new Map();
  const url = "https://k-tai.watch.impress.co.jp/docs/news/2151489.html";
  const id = await itemId(url);
  store.set("radar", JSON.stringify({
    since: "2026-10-27T00:00:00Z", handled: {}, daily: {}, notified: {},
    outbox: { [id]: { id, url, title: MISSED.siri, source: "radar-k-tai.watch.impress.co.jp", sourceName: "ケータイ Watch", secondary: true,
      via: "ktai-watch", found: url, publishedAt: "2026-10-27T04:40:24Z", detectedAt: "2026-10-27T04:45:00Z" } },
  }));
  const env = {
    REPO: "o/r", WORKFLOW: "auto_run.yml", GH_TOKEN: "x",
    FAST: { async get(k, type) { const v = store.get(k); return v == null ? null : type === "json" ? JSON.parse(v) : v; }, async put(k, v) { store.set(k, v); } },
  };
  const dispatches = [];
  const gh = async (path, init = {}) => {
    if (path.startsWith("/actions/workflows/auto_run.yml/runs")) return { workflow_runs: [] };
    if (path.endsWith("/dispatches")) return dispatches.push(JSON.parse(init.body)), null;
    throw new Error(`unexpected ${path}`);
  };
  const fetchImpl = async () => new Response("<html><body><article>短い</article></body></html>", { status: 200 });
  const rules = { ...RULES, sources: [] };
  await fastLane(env, new Date("2026-10-27T04:46:00Z"), { rules, fetch: fetchImpl, gh });
  assert.equal(dispatches.length, 1);
  assert.equal(dispatches[0].inputs.fast_url, url);
  const meta = JSON.parse(dispatches[0].inputs.fast_meta)[0];
  assert.deepEqual([meta.sourceName, meta.secondary, meta.via], ["ケータイ Watch", true, "ktai-watch"]);
  // 2回目は取り込み済み（radarTaken）なので重ねて登録しない
  const state = JSON.parse(store.get("state"));
  const log = { radar: [] };
  assert.equal(takeRadar(state, JSON.parse(store.get("radar")), new Date("2026-10-27T04:48:00Z"), log), false);
  assert.equal(log.radar.length, 0);
});

test("速報レーンの安全上限は30本（通常の記事の上限とは別）・ルールの正規表現が Python と JavaScript の両方で読める", () => {
  assert.equal(RULES.daily_max, 30);
  const R = compile(RULES);
  assert.ok(R.launch.test("Anthropic files S-1 with the SEC for proposed initial public offering"));
  assert.ok(RR.signal.test("Siri AI、日本語で提供開始"));
});

test("記事にならずに終わった登録は、見逃しチェック・手動の依頼なら起動し直す（Apple Creator Studio は生成に3回失敗して止まっていた）", async () => {
  const { reopenable } = await import("../src/fastlane.js");
  const done = (outcome, status = "done") => ({ status, outcome });
  assert.equal(reopenable(done("skipped"), { via: "manual" }), true);
  assert.equal(reopenable(done("skipped"), { via: "request:manual" }), true); // レーダーが依頼を outbox に置くときの via
  assert.equal(reopenable(done("skipped"), { via: "missed-check:itmedia-news" }), true);
  assert.equal(reopenable(done("skipped"), { via: "techmeme" }), false); // 見出しから見つけただけなら、判定済みの見送りは尊重する
  assert.equal(reopenable(done(undefined, "gave_up"), { via: "techmeme" }), true);
  assert.equal(reopenable(done("created"), { via: "manual" }), false);
  assert.equal(reopenable(done(undefined, "dispatched"), { via: "manual" }), false);
});

// 2026-10-28 の試運転（npm run radar-live）で見つけた誤判定の再発防止
test("同じページの言語違い（apple.com/newsroom と /jp/newsroom）は同じ出典・古い年月の公式リンクは使わない・提携の話は別の話題", async () => {
  const records = prepare([...BEFORE, {
    slug: "apple-14-inch-macbook-pro-m6", title: "Appleが「M6」搭載の新14インチMacBook Proを発表", text: "Appleが「M6」搭載の新14インチMacBook Proを発表",
    urls: ["https://www.apple.com/jp/newsroom/2026/10/apple-introduces-the-new-14-inch-macbook-pro-with-m6/"],
  }, {
    slug: "meta-muse-singapore-launch", title: "Metaの個人向けAIエージェント「Muse」 シンガポールで提供開始",
    text: "Metaの個人向けAIエージェント「Muse」 シンガポールで提供開始 | Muse / Meta", urls: ["https://about.fb.com/news/2026/10/muse-is-now-available-in-singapore/"],
  }], RR);
  const us = "https://www.apple.com/newsroom/2026/10/apple-introduces-the-new-14-inch-macbook-pro-with-m6/";
  assert.equal(covered("Apple unveils a 14-inch MacBook Pro with an M6", [us], records, RR)?.reason, "同じ出典URL");
  // 別の会社（NVIDIA）との提携の話は、Muse の記事があっても別の話題
  assert.equal(covered("NVIDIAとAMD、MetaとのAI分野での提携拡大を発表 「Muse」の強化や次世代インフラ構築で協力", [], records, RR), null);
  // gori.me の記事にあった前の年の発表へのリンクは一次情報にしない（報道を出典にする）
  const old = "https://www.apple.com/jp/newsroom/2025/10/apple-unveils-new-14-inch-macbook-pro-powered-by-the-m5-chip/";
  const gori = async (url) => (url.startsWith("https://gori.me") ? new Response(`<a href="${old}">前の型</a>`, { status: 200 }) : new Response("nf", { status: 404 }));
  const p = await resolvePrimary([{ ...cand("M6搭載14インチMacBook Proが登場", ["https://gori.me/macbookpro/999"]), publishedAt: "2026-10-27T15:00:00Z" }], RR, gori);
  assert.deepEqual([p.url, p.secondary], ["https://gori.me/macbookpro/999", true]);
});
