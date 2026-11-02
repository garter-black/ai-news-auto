// 2026-11-02 の追加: レーダーの見出し元（sources_registry.json の radar_sources）と、プラン改名・統合・上限変更の語。
// 取りこぼした「Claude Max 20x → Ultra」「ChatGPT の Chat と Work の統合」をレーダーなら拾えることを確かめる
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { mergeRegistry } from "../src/fastlane.js";
import { classify, compileRadar, outletOf, radarDue, withRadarSources } from "../src/radar.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const BASE = JSON.parse(readFileSync(join(ROOT, "scripts", "fast_lane_rules.json"), "utf-8"));
const REGISTRY = JSON.parse(readFileSync(join(ROOT, "scripts", "sources_registry.json"), "utf-8"));
const RR = compileRadar(mergeRegistry(BASE, REGISTRY));

const MISSED = [
  "Anthropic、「Claude」の「Max 20x」を「Ultra」に名称変更 月200ドルで5時間ごとの利用制限を撤廃",
  "ChatGPTの「Chat」と「Work」統合へ 利用制限はCodexと共通化",
  "OpenAI doubles ChatGPT Pro usage limits for Work and Codex",
  "SB Intuitions、国産LLM「Sarashina」に思考モデルを追加し公開",
  "LINEヤフー、Yahoo!ショッピングに生成AIの買い物アシスタントを提供開始",
];
const NOT = ["Microsoft の決算、AI 投資が重荷に", "楽天モバイルのキャンペーン情報まとめ", "NTT、新社長の就任を発表"];

test("取りこぼした話題の見出しが候補になる・決算やキャンペーンや人事はならない", () => {
  for (const t of MISSED) assert.equal(classify(t, RR).ok, true, t);
  for (const t of NOT) assert.equal(classify(t, RR).ok, false, t);
});

test("判定が Python（scripts/radar.py classify）と同じ", () => {
  const titles = [...MISSED, ...NOT];
  const r = spawnSync("python3", ["scripts/radar.py", "classify"], { input: JSON.stringify(titles.map((title) => ({ title }))), cwd: ROOT, encoding: "utf-8" });
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(titles.map((t) => classify(t, RR)), JSON.parse(r.stdout));
});

test("registry の radar_sources を足す（同じ slug・止めたものは足さない）。公式扱いにはしない", () => {
  const out = withRadarSources([{ slug: "a" }], [{ slug: "a" }, { slug: "b" }, { slug: "c", disabled: true }]);
  assert.deepEqual(out.map((s) => s.slug), ["a", "b"]);
  const slugs = RR.sources.map((s) => s.slug);
  for (const s of REGISTRY.radar_sources) assert.ok(slugs.includes(s.slug), s.slug);
  for (const s of REGISTRY.radar_sources) {
    const host = new URL(s.url).host.replace(/^www\./, "");
    assert.ok(!RR.primary_hosts.some((h) => h.host === host), `${host} は公式扱いにしない`);
  }
});

test("every 8分の見出し元は2回に1回だけ読む（1回の取得数を抑える）", () => {
  const src = { slug: "gigazine", every: 8 };
  const base = Date.UTC(2026, 10, 2, 0, 1);
  const hits = [0, 1, 2, 3].map((k) => radarDue(src, new Date(base + k * 240000)));
  assert.equal(hits.filter(Boolean).length, 2);
  assert.equal(radarDue({ slug: "techmeme" }, new Date(base)), true);
});

test("国内の競合（radar_no_cite_hosts）は一次情報が無いときの出典にもしない", () => {
  assert.equal(outletOf("https://ai.watch.impress.co.jp/docs/news/2153029.html", RR), null);
  assert.equal(outletOf("https://gigazine.net/news/20261102-x/", RR), null);
  assert.equal(outletOf("https://pc.watch.impress.co.jp/docs/news/1.html", RR)?.name, "PC Watch");
  assert.equal(outletOf("https://techcrunch.com/2026/11/02/x/", RR)?.name, "TechCrunch");
});
