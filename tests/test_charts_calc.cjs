const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 記事の図（lib/charts.ts）と計算機の式（lib/calc-math.ts）・記事に合う計算機（lib/calculators.ts）。Next.js を起動せずに検査する。
const root = path.resolve(__dirname, "..");
const cache = {};
function load(rel) {
  if (cache[rel]) return cache[rel];
  const file = path.join(root, rel);
  const out = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const mod = new Module(file, module);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(path.dirname(file));
  const original = mod.require.bind(mod);
  mod.require = (name) => (name.startsWith("@/lib/") ? load(`lib/${name.slice(6)}.ts`) : original(name));
  mod._compile(out, file);
  cache[rel] = mod.exports;
  return mod.exports;
}
const calc = load("lib/calc-math.ts");
const charts = load("lib/charts.ts");
const { calculatorForArticle, CALCULATORS } = load("lib/calculators.ts");
const fx = { rate: 157.9, date: "2026-11-02", source: "ECB", fallback: true };

test("ドル建てサブスクの円換算は終了とともに式を消した（2026-11-03・台帳 W12）", () => {
  assert.equal(calc.usdToYen, undefined);
});

test("AIプランの費用計算の式は終了とともに消した（2026-11-03）", () => {
  assert.equal(calc.compareBilling, undefined);
  assert.equal(calc.timesOf, undefined);
  assert.equal(calc.usageMultiple, undefined);
});

test("防衛特別所得税：財務省の大綱どおりの年ごとの税率", () => {
  assert.deepEqual(calc.surtax(150000, 2026), { defense: 0, reconstruction: 3150, total: 3150 });
  assert.deepEqual(calc.surtax(150000, 2027), { defense: 1500, reconstruction: 1650, total: 3150 });
  assert.deepEqual(calc.surtax(150000, 2040), { defense: 1500, reconstruction: 1650, total: 3150 });
  assert.deepEqual(calc.surtax(150000, 2048), { defense: 1500, reconstruction: 0, total: 1500 });
  // 改正前のきまり：復興 2.1% は 2037年まで
  assert.equal(calc.surtax(150000, 2037, true).total, 3150);
  assert.equal(calc.surtax(150000, 2040, true).total, 0);
  // 1円未満切り捨て
  assert.deepEqual(calc.surtax(12345, 2030), { defense: 123, reconstruction: 135, total: 258 });
  assert.equal(calc.baseFromWithheld(153150), 150000);
});

test("前と今の図：値は記事の文字のまま、棒は0から、差は数字から計算", () => {
  const html = charts.renderBeforeAfter({
    kind: "before_after",
    title: "口座数の変化",
    unit: "口座",
    groups: [{ label: "口座数の変化", items: [{ label: "2026年4月", value: 14e6, display: "1,400万口座" }, { label: "今回", value: 15e6, display: "1,500万口座", highlight: true }] }],
    source: { name: "楽天グループ", url: "https://example.com/" },
  });
  assert.match(html, /1,400万口座/);
  assert.match(html, /width="100%"[^>]*><\/rect><rect class="sc-fill" x="0" y="0" width="93.3%"/);
  assert.match(html, /\+100万口座（\+7\.1%）/);
  assert.match(html, /aria-hidden="true"/);
  assert.match(html, /出典：<a href="https:\/\/example.com\/"/);
  assert.equal(charts.deltaText(3.0, 3.2, "%"), "+0.2ポイント");
  assert.equal(charts.deltaText(3, 18, "拠点"), "+15拠点（6倍）");
});

test("比べる数字が2つ未満なら図を出さない", () => {
  assert.equal(charts.renderBeforeAfter({ kind: "before_after", title: "x", groups: [{ label: "x", items: [{ label: "前", value: 1, display: "1" }] }] }), null);
  assert.equal(charts.renderSeries({ kind: "series", title: "x", points: [{ label: "2025年", value: Number.NaN, display: "?" }, { label: "2026年", value: 2, display: "2" }] }), null);
  assert.equal(charts.renderLadder({ kind: "ladder", product: "claude" }, undefined, fx), null);
});

test("料金の段：ドルの製品はドル、円とドルが混ざる製品は円にそろえて換算に印", () => {
  const claude = {
    product: "claude", name: "Claude", checkedAt: "2026-11-03", sourceUrl: "https://claude.com/pricing", sourceTitle: "Claude 料金ページ", plans: [
      { id: "free", name: "Free", tier: "free", prices: [{ amount: 0, currency: "USD", billing: "monthly" }], limits: [] },
      { id: "ultra", name: "Ultra", tier: "top", prices: [{ amount: 200, currency: "USD", billing: "monthly" }], limits: [] },
      { id: "pro", name: "Pro", tier: "standard", prices: [{ amount: 17, currency: "USD", billing: "annual", billedAs: 200 }, { amount: 20, currency: "USD", billing: "monthly" }], limits: [] },
    ],
  };
  const { rows, currency } = charts.ladderRows(claude, fx);
  assert.equal(currency, "USD");
  assert.deepEqual(rows.map((r) => [r.label, r.value]), [["Free", 0], ["Pro", 20], ["Ultra", 200]]);
  const html = charts.renderLadder({ kind: "ladder", product: "claude", highlight: "ultra" }, claude, fx);
  assert.match(html, /sc-row sc-hl"><div class="sc-row-text"><span class="sc-label">Ultra/);
  assert.match(html, /2026-11-03確認/);
  const mixed = { ...claude, plans: [
    { id: "go", name: "Go", tier: "low", prices: [{ amount: 1400, currency: "JPY", billing: "monthly" }], limits: [] },
    { id: "pro", name: "Pro", tier: "top", prices: [{ amount: 200, currency: "USD", billing: "monthly" }], limits: [] },
  ] };
  const m = charts.ladderRows(mixed, fx);
  assert.equal(m.currency, "JPY");
  assert.equal(m.rows[1].converted, true);
  assert.match(charts.renderLadder({ kind: "ladder", product: "x" }, mixed, fx), /1ドル=157.9円/);
});

test("図は指定の章の下、計算機の印は料金の章の終わりに入る", () => {
  const html = '<h2 id="a">概要</h2><p>a</p><h2 id="b">料金は？</h2><p>b</p><h2 id="c">まとめ</h2><p>c</p>';
  const out = charts.insertFigures(html, [{ html: "<figure>F</figure>", afterHeading: "概要", kind: "before_after" }]);
  assert.equal(out, '<h2 id="a">概要</h2><p>a</p><figure>F</figure><h2 id="b">料金は？</h2><p>b</p><h2 id="c">まとめ</h2><p>c</p>');
  const ladder = charts.insertFigures(html, [{ html: "<figure>L</figure>", afterHeading: null, kind: "ladder" }]);
  assert.ok(ladder.includes("<p>b</p><figure>L</figure><h2"));
  const slot = charts.markCalculatorSlot(html, /料金/);
  assert.ok(slot.includes(`<p>b</p>${charts.CALC_SLOT}<h2 id="c">`));
  assert.equal(charts.markCalculatorSlot(html, /存在しない/), html);
});

test("記事に合う計算機は1つだけ", () => {
  const base = { title: "", tags: [], genreSlug: "ai" };
  assert.equal(calculatorForArticle({ ...base, title: "2027年から防衛特別所得税" }, ["claude"]).kind, "defense-tax");
  // AIプランの費用計算（ai-plan-cost）は 2026-11-03 に終了。料金の段の図がある記事には計算機を置かない（図に円の目安がある）
  const plan = calculatorForArticle({ ...base, title: "Claude Ultra", charts: [{ kind: "ladder", product: "claude", highlight: "ultra" }], verdict: { price: "月200ドル" } }, ["claude"]);
  assert.equal(plan, null);
  assert.equal(calculatorForArticle({ ...base, title: "Claude Ultra", charts: [{ kind: "ladder", product: "claude", highlight: "ultra" }] }), null);
  // ドル建てサブスクの円換算（usd-jpy）は 2026-11-03 に終了（オーナー「このページもいらない」・台帳 W12）。ドルの料金の記事にも置かない
  assert.equal(calculatorForArticle({ ...base, verdict: { price: "月20ドル" } }, []), null);
  assert.deepEqual(Object.keys(CALCULATORS).sort(), ["defense-tax"]);
});

test("同じクラスの製品との比較（rivals）は項目ごとに棒の基準を分け、変化の数字を出さない", () => {
  const chart = {
    kind: "rivals",
    title: "同じクラスの製品との比較",
    groups: [
      { label: "重さ", unit: "kg", items: [{ label: "Honda RN0", value: 24, display: "約24kg", highlight: true }, { label: "Turbo Levo 4 Comp", value: 24.41, display: "24.41kg" }] },
      { label: "最大トルク", unit: "Nm", items: [{ label: "Honda RN0", value: 100, display: "100Nm", highlight: true }, { label: "Turbo Levo 4 Comp", value: 105, display: "105Nm" }] },
      { label: "1つだけ", items: [{ label: "A", value: 1, display: "1" }] },
    ],
    afterHeading: "同じクラスの製品と比べると？",
    source: { name: "各社の公式ページ・発表資料", checkedAt: "11/3" },
  };
  const fig = charts.renderChart(chart, () => undefined, fx);
  assert.ok(fig);
  assert.equal(fig.kind, "rivals");
  assert.equal(fig.afterHeading, "同じクラスの製品と比べると？");
  assert.match(fig.html, /data-chart="rivals"/);
  assert.match(fig.html, /約24kg/);
  assert.match(fig.html, /sc-hl/);
  assert.doesNotMatch(fig.html, /1つだけ/);
  assert.doesNotMatch(fig.html, /sc-delta">[^<]+</);
  // 105Nm が最大トルクの棒の基準（100%）。重さの 24.41kg も重さの中で 100%
  assert.equal((fig.html.match(/width="100%" height="10" rx="2"><\/rect><\/svg>/g) || []).length, 2);
  assert.equal(charts.renderChart({ ...chart, groups: [chart.groups[2]] }, () => undefined, fx), null);
});
