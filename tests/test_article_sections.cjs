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
    const { markdown, hidden } = cleanSections(c.markdown, c.titles);
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

test("「〜しましょう」は「〜するとよいでしょう」に（Python の門と同じ見本。2026-11-03 オーナーのルール A17）", () => {
  const { professionalSentence, cleanTake } = loaded.exports;
  const reg = JSON.parse(fs.readFileSync(path.join(root, "tests/fixtures/register_cases.json"), "utf8"));
  for (const [sentence, want] of reg.sentences) assert.equal(professionalSentence(sentence), want, sentence);
  for (const [take, want] of reg.takes) assert.equal(cleanTake(take), want, take);
});

test("表の「記載なし」は空欄、全部空の列は列ごと消す。「元記事には書かれていません」は文ごと消す（W11・A2）", () => {
  const md =
    "## 他社との比較\n\n| 項目 | A | B | C |\n|---|---|---|---|\n| 重さ | 公式に記載なし | 1.2kg | 公式に記載なし |\n| 価格 | 10万円 | 12万円 | — |\n\n" +
    "- 「公式に記載なし」は、その会社の公式ページに書かれていない項目です。\n\n" +
    "## 料金\n\n本体は10万円です（元記事では税の扱いは言及なし）。保証の料金は、元記事には書かれていません。\n";
  const { markdown } = cleanSections(md);
  assert.ok(!markdown.includes("記載なし"));
  assert.ok(!markdown.includes("元記事"));
  assert.ok(markdown.includes("| 重さ |  | 1.2kg |"));
  assert.ok(markdown.includes("本体は10万円です。"));
});

test("[A19] 本文の頭の「要約すると、」はリードの言い直しなので外す（2026-11-04 の点検。新しい記事5本中4本）", () => {
  const { stripSummaryOpener } = loaded.exports;
  const lede = "Amazon BedrockのAIモデル「Kimi K3」で、推論処理をインド国内に限って実行できるプロファイルが加わりました。処理先はムンバイとハイデラバードの2リージョンです。";
  const body = "要約すると、AIモデル「Kimi K3」をAmazon Bedrock経由で使うとき、処理をインド国内だけに限れるようになりました。\n\nAmazon Bedrockは、AWSのサービスです。\n\n## 使い方\n\nA";
  assert.equal(stripSummaryOpener(body, lede), "Amazon Bedrockは、AWSのサービスです。\n\n## 使い方\n\nA");
  // リードと重ならない「それが何か」の文は残し、書き出しの言葉だけ外す
  const other = "要約すると、半導体工場の計算待ちをAIで縮めた事例集です。新製品の発表ではありません。\n\n本文";
  assert.equal(stripSummaryOpener(other, lede), "半導体工場の計算待ちをAIで縮めた事例集です。新製品の発表ではありません。\n\n本文");
  // 見出しの下でも同じ。頭以外の「要約すると」には触らない
  assert.equal(stripSummaryOpener("## 概要\n\n要約すると、事例集です。", lede), "## 概要\n\n事例集です。");
  assert.equal(stripSummaryOpener("本文。\n\n要約すると、Aです。", lede), "本文。\n\n要約すると、Aです。");
});

test("[A19] 古い記事の「## 概要」の章は出さない（リードと要点のくり返し）", () => {
  const { dropOverviewSections } = loaded.exports;
  const md = "## 概要\n\nリードと同じ話です。\n\n## 発表の内容\n\nA社は10月1日に発表しました。\n\n## まとめ\n\nB";
  assert.equal(dropOverviewSections(md), "## 発表の内容\n\nA社は10月1日に発表しました。\n\n## まとめ\n\nB");
  assert.equal(dropOverviewSections("## 発表の内容\n\nA"), "## 発表の内容\n\nA");
});

test("[A24] 行の頭の立場の名前（「- 会社員として：〜」）は出さず、事実のある値だけ残す", () => {
  const md = "## 活用点\n\n- 会社員として：社内の調達で手間が減ります。\n- 副業・独立を考えている人：参考になります。\n";
  const { markdown, hidden } = cleanSections(md);
  assert.ok(!/会社員として|副業・独立/.test(markdown), markdown);
  assert.deepEqual(hidden, ["活用点"]);
});

test("[A36] 記事へのリンクの文字が slug のままなら、リンク先の題名にする（2026-11-04 の点検）", () => {
  const { titleSlugLinks } = loaded.exports;
  const md = "- 11月2日：ARTEXの悪用を確認（[artex-agentic-offensive-frameworks](/articles/artex-agentic-offensive-frameworks)）\n- [題名のある記事](/articles/a-b-c)";
  const out = titleSlugLinks(md, (s) => (s === "artex-agentic-offensive-frameworks" ? "Google、[ARTEX]の悪用を確認" : undefined));
  assert.equal(out, "- 11月2日：ARTEXの悪用を確認（[Google、ARTEXの悪用を確認](/articles/artex-agentic-offensive-frameworks)）\n- [題名のある記事](/articles/a-b-c)");
  assert.equal(titleSlugLinks("[x-y-z](/articles/x-y-z)", () => undefined), "[関連記事](/articles/x-y-z)");
});
