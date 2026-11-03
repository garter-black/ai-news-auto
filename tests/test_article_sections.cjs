const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 記事の章の表示の門（lib/article-sections.ts）。生成の門 scripts/section_gate.py と同じ結果になるかを、同じ見本で確かめる
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/article-sections.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded.paths = Module._nodeModulePaths(path.dirname(sourcePath));
loaded._compile(compiled, sourcePath);
const { cleanSections, displayHeading, retitleHtml, tocOf } = loaded.exports;
const cases = JSON.parse(fs.readFileSync(path.join(root, "tests/fixtures/section_gate_cases.json"), "utf8"));

test("Python の門（scripts/section_gate.py）と同じ結果になる", () => {
  for (const c of cases) {
    const { markdown, hidden } = cleanSections(c.markdown);
    assert.equal(markdown, c.expected, c.name);
    assert.deepEqual(hidden, c.hidden, c.name);
  }
});

test("中身なしの「ライバルと比べてどう？」は出さない（2026-11-03 オーナー指摘）", () => {
  const md = "## 概要\n\nAです。\n\n## ライバルと比べてどう？\n\n元記事には、他社製品との比較は書かれていません。ここは編集部注です。\n\n性能や使い勝手の優劣は、元記事からは判断できません。\n";
  const { markdown, hidden } = cleanSections(md);
  assert.deepEqual(hidden, ["ライバルと比べてどう？"]);
  assert.ok(!markdown.includes("ライバル"));
});

test("決まった型の見出しは、記事ごとの見出し → 普通の見出しの順に言い換える", () => {
  const titles = { body: { "自分に関係ある？": "ランクルFJを検討中の人は何が変わる？" } };
  assert.equal(displayHeading("自分に関係ある？", "body", titles), "ランクルFJを検討中の人は何が変わる？");
  assert.equal(displayHeading("もう使える？取引できる？", "body", titles), "提供時期と利用方法");
  assert.equal(displayHeading("何がすごいの？", "easy"), "何が新しいのか");
  assert.equal(displayHeading("記事ごとの見出し", "body"), "記事ごとの見出し");
  // 壊れた保存値（長すぎる・HTML）は使わない
  assert.equal(displayHeading("料金は？", "body", { body: { "料金は？": "<b>x</b>" } }), "料金");
  for (const robotic of ["自分に関係ある？", "もう使える？取引できる？", "ライバルと比べてどう？", "料金は？", "日本で使える？"])
    assert.notEqual(displayHeading(robotic, "body"), robotic, robotic);
});

test("描画済みの見出しを言い換え、目次と同じ id を付ける", () => {
  const md = "## 概要\n\nA\n\n## 自分に関係ある？\n\nB\n";
  const html = "<h2>概要</h2><p>A</p><h2>自分に関係ある？</h2><p>B</p>";
  const titles = { body: { "自分に関係ある？": "買い替えを考える人への影響" } };
  const out = retitleHtml(html, "body", titles);
  assert.equal(out, '<h2 id="sec-1">概要</h2><p>A</p><h2 id="sec-2">買い替えを考える人への影響</h2><p>B</p>');
  assert.deepEqual(tocOf(md, titles), [
    { id: "sec-1", text: "概要" },
    { id: "sec-2", text: "買い替えを考える人への影響" },
  ]);
});
