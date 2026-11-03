const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 図鑑（/products）の「中身があるか」の判定と文の掃除（lib/product-substance.ts）、ヘッダーの定番ページ（lib/nav.ts）。
// 2026-11-03 オーナーの指摘「内容がすごく薄い」「このページもいらない」（台帳 W14・W13）
const root = path.resolve(__dirname, "..");
function load(rel) {
  const file = path.join(root, rel);
  const out = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const mod = new Module(file, module);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(path.dirname(file));
  mod._compile(out, file);
  return mod.exports;
}
const sub = load("lib/product-substance.ts");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
const json = (rel) => JSON.parse(read(rel));

test("答えになっていない文は文ごと消す（記載なし・元記事には・未定）", () => {
  assert.equal(sub.cleanProductText("記載なし"), "");
  assert.equal(sub.cleanProductText("発表の時点では、日本国内での直接的な利用手順について元記事に記載はありません。"), "");
  assert.equal(sub.cleanProductText("まずは一部の担当者向けに提供が始まっています。一般向けの提供時期は未定です。"), "まずは一部の担当者向けに提供が始まっています。");
  assert.equal(sub.cleanProductText("API の従量課金です（元記事では無料枠の言及なし）。"), "API の従量課金です。");
  assert.deepEqual(sub.cleanProductList([{ text: "具体的な価格の言及なし" }, { text: "最大12倍の高速化。" }]), [{ text: "最大12倍の高速化。" }]);
});

test("よくある質問の問いは落ち着いた「です・ます」", () => {
  assert.equal(sub.politeQuestion("無料で使える？"), "無料で使えますか？");
  assert.equal(sub.politeQuestion("通常のチャットでも利用枠は消費される？"), "通常のチャットでも利用枠は消費されますか？");
  assert.equal(sub.politeQuestion("上限に達したときはどうなる？"), "上限に達したときはどうなりますか？");
  assert.equal(sub.politeQuestion("利用枠の確認方法は？"), "利用枠の確認方法は？");
  assert.equal(sub.politeQuestion("いつから使える?"), "いつから使えますか？");
});

test("一覧に出すのは公式の料金・日付つきの動き・中身がそろった製品だけ", () => {
  const ok = { overview: "Codexは、OpenAIが提供するAIプログラミングツールです。指示からコードを書き、試作やテストまで進めます。", officialPriceRows: 2, datedEvents: 3, liveArticles: 20, features: 5 };
  assert.deepEqual(sub.productListing(ok), { listed: true, reasons: [] });
  assert.equal(sub.productListing({ ...ok, officialPriceRows: 0 }).listed, false);
  assert.equal(sub.productListing({ ...ok, datedEvents: 1, liveArticles: 2 }).listed, false);
  assert.equal(sub.productListing({ ...ok, overview: "短い。" }).listed, false);
  assert.equal(sub.productListing({ ...ok, features: 1 }).listed, false);
});

test("つなぎが指す料金の行を数える（料金ページに無いモデルは数えない）", () => {
  const pricing = {
    chatgpt: { plans: [{ id: "business-standard", prices: [{}] }, { id: "empty", prices: [] }] },
    "gemini-api": { models: [{ id: "gemini-4-argon", status: "listed" }, { id: "gemini-4-carbon", status: "not_listed" }] },
  };
  assert.equal(sub.officialPriceRowCount({ pricing: { product: "chatgpt", plans: ["business-standard", "empty", "nope"] } }, pricing), 1);
  assert.equal(sub.officialPriceRowCount({ pricing: { product: "gemini-api", models: ["gemini-4-carbon"] } }, pricing), 0);
  assert.equal(sub.officialPriceRowCount(undefined, pricing), 0);
});

test("図鑑のページは記事の写真・素材を使わず、決まった型の問いかけの見出しを使わない", () => {
  const page = read("app/products/[slug]/page.tsx");
  const index = read("app/products/page.tsx");
  for (const src of [page, index]) {
    assert.doesNotMatch(src, /\bThumb\b|coverPath|thumbnail/, "記事の写真・カバー画像を図鑑に出さない");
    assert.match(src, /ProductVisual/);
  }
  for (const heading of sub.TEMPLATE_PRODUCT_HEADINGS) assert.ok(!page.includes(`"${heading}"`), heading);
  // 一覧に出さない製品は noindex
  assert.match(page, /listing\.listed \? \{\} : \{ robots: \{ index: false/);
  // 文字の無い図形（公式画像が無いとき）
  assert.doesNotMatch(read("components/ProductVisual.tsx"), /<text|company\.mark|\{company\.name\}/);
});

test("ドル建てサブスクの円換算は終了（ページ・ナビ・フッター・サイトマップ・記事の埋め込み）", () => {
  assert.equal(fs.existsSync(path.join(root, "app/tools/usd-jpy")), false);
  assert.equal(fs.existsSync(path.join(root, "app/tools/page.tsx")), false, "ツールが1つだけの一覧ページは作らない（/tools は転送）");
  for (const rel of ["lib/nav.ts", "components/Footer.tsx", "lib/sitemap.ts", "lib/calculators.ts", "components/calc/CalculatorBox.tsx", "lib/page-share.ts"]) {
    assert.doesNotMatch(read(rel), /"\/tools\/usd-jpy"|"usd-jpy"/, rel);
  }
  assert.doesNotMatch(read("lib/nav.ts"), /href: "\/tools"/);
  assert.match(read("cloudflare/worker.ts"), /"\/tools\/usd-jpy": \{/);
  assert.match(read("next.config.ts"), /source: "\/tools", destination: "\/tools\/defense-tax"/);
});

test("公式データのつなぎは実在する行を指す", () => {
  const { items } = json("content/product_sources.json");
  for (const [slug, src] of Object.entries(items)) {
    assert.ok(fs.existsSync(path.join(root, `content/products/${slug}.json`)), slug);
    const pricing = json(`content/pricing/${src.pricing.product}.json`);
    const rows = sub.officialPriceRowCount(src, { [src.pricing.product]: pricing });
    assert.ok(rows >= 1, `${slug}: 公式の料金の行が無い`);
  }
});

test("日本での提供の注記は「です・ます」で出す", () => {
  assert.equal(sub.politeNote("API の対応国の一覧に日本がある。料金はドル建て（税別）。"), "API の対応国の一覧に日本があります。料金はドル建て（税別）です。");
  assert.equal(sub.politeNote("円での月額は公式ページでは案内されていない。ヘルプが日本語で公開されている。"), "円での月額は公式ページでは案内されていません。ヘルプが日本語で公開されています。");
});
