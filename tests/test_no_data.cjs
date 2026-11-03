const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 「記載なし」を読者に見せない（lib/no-data.ts・components/PriceTable.tsx。2026-11-03 オーナーのルール W11）
const root = path.resolve(__dirname, "..");
function load(rel) {
  const file = path.join(root, rel);
  const out = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  m._compile(out, file);
  return m.exports;
}
const { withoutNoData, withoutNoDataList, hasValue } = load("lib/no-data.ts");

test("「記載なし」の文・かっこ書きだけを消す", () => {
  assert.equal(withoutNoData("公式ページに記載なし"), "");
  assert.equal(withoutNoData("ページに税の記載なし"), "");
  assert.equal(withoutNoData("個人向けは税の扱いの記載なし。法人向けは「価格には消費税は含まれていません」（公式ページ）"), "法人向けは「価格には消費税は含まれていません」（公式ページ）");
  assert.equal(withoutNoData("Google の日本向けページの円表示（税の扱いはページに記載なし）"), "Google の日本向けページの円表示");
  assert.equal(withoutNoData("100種類以上の言語。話者ごとに整理。日本語の精度の数値は記載なし"), "100種類以上の言語。話者ごとに整理。");
  assert.equal(withoutNoData("月額2,130円"), "月額2,130円");
  assert.deepEqual(withoutNoDataList(["公式ページに詳細な仕様の記載なし", "1 ユーザー用"]), ["1 ユーザー用"]);
});

test("値の無いセル（空・—・記載なし）は値なし。値の無い列は料金表で隠す", () => {
  for (const empty of ["", "—", "-", "記載なし", "料金ページに記載なし", null, undefined]) assert.equal(hasValue(empty), false, String(empty));
  for (const value of ["¥2,130/月", "約3,200円", "$3"]) assert.equal(hasValue(value), true, value);
  const table = fs.readFileSync(path.join(root, "components/PriceTable.tsx"), "utf8");
  // 円の目安・上限・キャッシュの列は、値のある行が1つも無ければ列ごと出さない。「料金ページに記載なし」「—」の文字を表に書かない
  assert.match(table, /const showJpy = rows\.some\(\(r\) => hasValue\(r\.jpyText\)\)/);
  assert.match(table, /const showLimits = rows\.some/);
  assert.match(table, /const showCache = rows\.some/);
  assert.ok(!table.includes("料金ページに記載なし</"), "料金ページに記載なしの行を出している");
  assert.ok(!/\|\| "—"/.test(table), "空のセルに「—」を出している");
  assert.match(table, /r\.kind !== "api" \|\| r\.listed/);
});

test("料金表・比較ガイド・製品の基本情報は、読み込みで「記載なし」を取り除く", () => {
  const ladder = fs.readFileSync(path.join(root, "lib/pricing-ladder.ts"), "utf8");
  assert.match(ladder, /taxNote: withoutNoData\(data\.taxNote\)/);
  assert.match(ladder, /limits: withoutNoDataList\(plan\.limits\)/);
  assert.match(fs.readFileSync(path.join(root, "lib/buying-guides.ts"), "utf8"), /withoutNoDataGuide\(JSON\.parse/);
  assert.match(fs.readFileSync(path.join(root, "lib/products.ts"), "utf8"), /value: withoutNoData\(f\.value\)/);
  assert.ok(!fs.readFileSync(path.join(root, "lib/choose-data.ts"), "utf8").includes("記載なし"), "選び方の表に「記載なし」がある");
});
