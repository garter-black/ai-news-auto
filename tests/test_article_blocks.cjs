// 記事ページの欄の並びと重なり（2026-11-03・台帳 A28）。
// 要約の欄は「この記事のポイント」1つだけ（要約版の「要点」は出さない）・目次は本文の前（章が3つ以下なら出さない）・
// 「仕事とお金への影響」は記事ごとの中身があるときだけ、ニュースの種類に合った見出しで出す。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

function load(rel) {
  const file = path.join(__dirname, "..", rel);
  const compiled = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  m._compile(compiled, file);
  return m.exports;
}
const { dropSummarySections, displayHeading, isNonanswerTitle } = load("lib/article-sections.ts");
const { concreteWorkMoney, workMoneyHeading, WORK_MONEY_HEADING } = load("lib/work-money.ts");
const page = fs.readFileSync(path.join(__dirname, "../app/articles/[slug]/page.tsx"), "utf8");

test("要約版の「ひとことで言うと（要点）」の章は、上の「この記事のポイント」とくり返しなので外す", () => {
  const md = "## ひとことで言うと\n\nAがBを出しました。\n\n## 答えを出さないAIを授業に\n\n本文。\n\n## 要点\n\nx";
  const out = dropSummarySections(md);
  assert.ok(!/ひとことで言うと|## 要点/.test(out), out);
  assert.match(out, /## 答えを出さないAIを授業に\n\n本文。/);
});

test("目次は本文（要約版・詳細版）の前、章が3つ以下の記事には出さない", () => {
  const flow = page.indexOf('<div id="article-flow"');
  const toc = page.indexOf('id="toc-heading"');
  const easy = page.indexOf('className="read-easy"');
  const body = page.indexOf("本文（前半）");
  assert.ok(flow > 0 && flow < toc && toc < easy && easy < body, "目次が本文の前にない");
  assert.match(page, /headings\.length > 3 &&/);
  assert.match(page, /dropSummarySections\(/);
});

test("「仕事とお金への影響」は記事ごとの中身（数字・名前）がある点だけ。一般論・要点のくり返し・影響が小さい記事は出さない", () => {
  const highlights = ["Classroomの学習モードは世界で今日から使える。"];
  assert.equal(concreteWorkMoney({ relevance: "low", points: ["Geminiが月20ドルで使える。"] }, highlights), null);
  assert.equal(concreteWorkMoney({ relevance: "medium", points: ["会社員や個人事業主など、将来年金を受け取るすべての人に関係する取り組みです。"] }, highlights), null);
  assert.equal(concreteWorkMoney({ relevance: "high", points: ["Classroomの学習モードは世界で今日から使える。"] }, highlights), null);
  const kept = concreteWorkMoney({ relevance: "high", points: ["Apple株の配当は1株0.27ドル。", "業務の効率化に役立つ可能性があります。"] }, highlights);
  assert.deepEqual(kept.points, ["Apple株の配当は1株0.27ドル。"]);
});

test("欄の見出しはニュースの種類（無ければジャンル）に合わせる", () => {
  assert.equal(workMoneyHeading("earnings"), "株主・取引先への影響");
  assert.equal(workMoneyHeading("product"), "使う人への影響");
  assert.equal(workMoneyHeading(undefined, "public"), "対象になる人への影響");
  assert.equal(workMoneyHeading(undefined, "business"), WORK_MONEY_HEADING);
  assert.equal(workMoneyHeading(undefined, "tech-gadget", "Apple、7〜9月期の売上高が12％増の1145億ドル", ["決算"]), "株主・取引先への影響");
  assert.match(page, /heading=\{workMoneyHeading\(article\.newsType, article\.genreSlug, article\.title, article\.tags\)\}/);
});

test("答えになっていない見出し（「〜は不明」）は普通の見出しに、事実の見出し（「〜を変えない」「追加料金なし」）はそのまま（台帳 A24）", () => {
  assert.equal(displayHeading("枠が新設かどうかは不明", "body"), "補足");
  assert.equal(displayHeading("Slurmとのつなぎ方はアプリを変えない", "body"), "Slurmとのつなぎ方はアプリを変えない");
  assert.equal(isNonanswerTitle("追加料金なし"), false);
  assert.equal(isNonanswerTitle("比べられる数字は示されていない"), true);
  assert.equal(isNonanswerTitle("まだ分からないこと"), false);
});

test("トップの一番上の記事は公式の発表から（報道をもとにした記事を主役にしない。台帳 W17）", () => {
  const home = fs.readFileSync(path.join(__dirname, "../app/page.tsx"), "utf8");
  assert.match(home, /!isNewsOutletChannel\(a\.channelSlug\)/);
});
