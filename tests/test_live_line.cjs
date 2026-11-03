const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 台帳 W23（2026-11-03 オーナー決定）: ヘッダー下の赤い「速報」の帯は本当の速報だけ。ほかは紺の「最新」。
// 判定は画像の赤い「速報」と同じ（lib/cover-path.ts の isFastNews。台帳 A9）＋載せてから6時間以内。
const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
function load(rel) {
  const file = path.join(root, rel);
  const out = ts.transpileModule(read(rel), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const m = new Module(file, module);
  m.filename = file;
  m._compile(out, file);
  return m.exports;
}
const { liveLabel, LIVE_FRESH_MS } = load("lib/live-line.ts");
const { isFastNews } = load("lib/cover-path.ts");
const NOW = Date.parse("2026-11-03T12:00:00Z");
const ago = (h) => new Date(NOW - h * 3_600_000).toISOString();

test("速報でない記事は、新しくても「最新」", () => {
  assert.equal(liveLabel({ fast: false, publishedAt: ago(0.2) }, NOW), "最新");
  assert.equal(liveLabel({ publishedAt: ago(0.2) }, NOW), "最新");
});

test("速報の記事でも、6時間を過ぎたら「最新」（「19時間前」に赤い速報を付けない）", () => {
  assert.equal(liveLabel({ fast: true, publishedAt: ago(1) }, NOW), "速報");
  assert.equal(liveLabel({ fast: true, publishedAt: ago(19) }, NOW), "最新");
  assert.equal(LIVE_FRESH_MS, 6 * 3_600_000);
  assert.equal(liveLabel({ fast: true, publishedAt: "壊れた日付" }, NOW), "最新");
});

test("速報かどうかは画像の札と同じ isFastNews（breaking か公式発表から60分以内）", () => {
  assert.equal(isFastNews({ breaking: true }), true);
  assert.equal(isFastNews({ speedMinutes: 12 }), true);
  assert.equal(isFastNews({}), false);
  assert.equal(isFastNews({ breaking: true, type: "roundup" }), false);
  const header = read("components/Header.tsx");
  assert.match(header, /fast:\s*isFastNews\(a\)/, "ヘッダーが帯に速報かどうかを渡していない");
  const line = read("components/LiveLine.tsx");
  assert.match(line, /liveLabel\(item, now\)/);
  // 札の文字を決め打ちの「速報」にしない
  assert.doesNotMatch(line, />\s*速報\s*</, "帯の札が決め打ちの「速報」になっている");
  // 一覧の索引に breaking が入っている（帯・カードが判定に使う）
  assert.match(read("lib/article-index.mjs"), /breaking:\s*a\.breaking/);
});
