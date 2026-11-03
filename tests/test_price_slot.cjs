const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 記事の料金の章の表 → 毎日更新の料金表（lib/price-slot.ts）。Next.js を起動せずに検査する。
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/price-slot.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded.paths = Module._nodeModulePaths(path.dirname(sourcePath));
loaded.require = (name) => {
  if (name === "@/lib/pricing-ladder")
    return {
      getLadder: (id) => (id === "claude" || id === "copilot" ? { rows: [{}] } : null),
      isOtherCopilot: (text) => /github/i.test(text) && !/microsoft\s*365|m365/i.test(text),
      getComparison: (tier) => ({ rows: tier === "top" ? [{ company: "anthropic" }, { company: "openai" }] : [{ company: "anthropic" }] }),
    };
  throw new Error(name);
};
loaded._compile(compiled, sourcePath);
const { pricingForArticle, withPriceSlot, markPriceSlot, PRICE_SLOT } = loaded.exports;

const BODY = [
  "## 料金は？",
  "Ultraは月200ドルです。",
  "",
  "### Claudeのプランと料金（公式の料金と円の目安）",
  "",
  "| プラン | 料金（公式） |",
  "|---|---|",
  "| Pro | $20/月 |",
  "",
  "出典：Claudeの公式ページ／確認日：2026-11-03",
  "円は1ドル=157.90円で計算した目安です。",
  "",
  "## 日本で使える？",
  "使えます。",
].join("\n");

test("差し込んだ表だけを印に置き換え、見出しと前後の章は残す", () => {
  const out = withPriceSlot(BODY);
  assert.doesNotMatch(out, /### Claudeのプランと料金/);
  assert.match(out, /SAISOKU-PRICE-TABLE-SLOT/);
  assert.doesNotMatch(withPriceSlot(BODY, false), /SAISOKU-PRICE-TABLE-SLOT|\| Pro \|/);
  assert.doesNotMatch(out, /\| Pro \|/);
  assert.doesNotMatch(out, /出典：Claude/);
  assert.match(out, /## 日本で使える？\n使えます。/);
  assert.equal(withPriceSlot("## 料金は？\n月200ドル"), "## 料金は？\n月200ドル");
});

test("印の段落を PRICE_SLOT にする", () => {
  assert.equal(markPriceSlot("<h3>x</h3><p>SAISOKU-PRICE-TABLE-SLOT</p><h2>y</h2>"), `<h3>x</h3>${PRICE_SLOT}<h2>y</h2>`);
});

test("料金データのある製品だけ。比較は2社以上のときだけ", () => {
  assert.deepEqual(pricingForArticle({ pricingProduct: "claude", pricingCompare: "plan:top" }), {
    product: "claude",
    compare: { tier: "top", kind: "plan" },
  });
  assert.deepEqual(pricingForArticle({ pricingProduct: "claude", pricingCompare: "plan:high" }), { product: "claude", compare: null });
  assert.equal(pricingForArticle({ pricingProduct: "unknown" }), null);
  assert.equal(pricingForArticle({}), null);
});

test("[A32] GitHub Copilot の記事に Microsoft 365 の Copilot の料金表を出さない（2026-11-04 の点検）", () => {
  const github = { pricingProduct: "copilot", newsType: "product", title: "GitHubがJetBrainsの「Air」にCopilotを提供", lede: "GitHub Copilotを選べます。料金はCopilotのプランから消費されます。" };
  // 題名は「Copilot」だけでも、GitHub の Copilot の話なら出さない（2026-11-04 に見つかった記事そのもの）
  assert.equal(pricingForArticle(github), null);
  assert.equal(pricingForArticle({ ...github, title: "GitHub Copilot、料金プランを変更", lede: "GitHub Copilotのプランが変わります。" }), null);
  assert.equal(pricingForArticle({ pricingProduct: "copilot", newsType: "pricing", title: "Microsoft 365 Copilot、料金を改定", lede: "Copilotのプランが変わります。" })?.product, "copilot");
  assert.ok(github);
});
