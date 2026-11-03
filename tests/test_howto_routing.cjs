const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// Next.js を起動せず、本体の選択関数と実際のガイド設定をオフラインで検査する。
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/howto.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded.paths = Module._nodeModulePaths(path.dirname(sourcePath));
const requireOriginal = loaded.require.bind(loaded);
loaded.require = (name) => {
  if (name === "@/lib/content-fs") return fs;
  if (name === "@/lib/articles") return { getAllArticles: () => [] };
  // 文末の言い換え（lib/polite-text.ts）は振り分けに関係しない
  if (name === "@/lib/polite-text") return { politeDeep: (value) => value };
  return requireOriginal(name);
};
loaded._compile(compiled, sourcePath);
const { howtoForArticle } = loaded.exports;
const readGuide = (slug) => JSON.parse(fs.readFileSync(path.join(root, "content/howto", `${slug}.json`), "utf8"));
const startup = readGuide("dify-1-18-startup-check");
const cost = readGuide("dify-free-api-cost");
const chatgptFree = readGuide("chatgpt-free-plan");
const chatgptHowto = readGuide("chatgpt-how-to-use");
const gemini = readGuide("gemini-free-plan");
const guides = [startup, cost, chatgptFree, chatgptHowto, gemini];
const pick = (title, seoTitle = "", candidates = guides) => howtoForArticle({ title, seoTitle }, candidates)?.slug;

test("Difyの料金・無料枠・APIキーの疑問を料金ガイドへ案内する", () => {
  for (const title of [
    "Dify Cloudの無料クレジットとAPI料金が変更",
    "Dify APIキーの利用条件を確認",
    "Dify Cloud free credits",
    "Dify API cost update",
    "Dify 1.18.0のAPI料金",
  ]) assert.equal(pick(title), cost.slug, title);
});

test("Difyの起動・版数・更新は起動ガイドへ案内する", () => {
  for (const title of ["Dify 1.18.0が起動しない", "Dify self-host upgrade", "Dify version 1.18.0", "Difyのapiコンテナが起動しない"]) {
    assert.equal(pick(title), startup.slug, title);
  }
  // 並び順が入れ替わっても、起動の疑問には起動ガイドを選ぶ。
  assert.equal(pick("Dify startup check", "", [cost, startup]), startup.slug);
});

test("疑問が特定できないDify記事は従来の最初の一致を維持する", () => {
  assert.equal(pick("Difyが新機能を発表"), startup.slug);
  assert.equal(pick("Difyが新機能を発表", "", [cost, startup]), cost.slug);
});

test("SEOタイトルの料金意図も読む", () => {
  assert.equal(pick("Dify Cloudの変更点", "API課金と費用"), cost.slug);
});

test("他ツールの既存案内と無関係な記事を変えない", () => {
  assert.equal(pick("ChatGPTの無料枠"), chatgptFree.slug);
  assert.equal(pick("ChatGPTの使い方"), chatgptFree.slug);
  assert.equal(pick("GeminiのAPI料金"), gemini.slug);
  assert.equal(pick("自治体の無料相談"), undefined);
});

test("不正または空の意図条件は既存fallbackを壊さない", () => {
  for (const match of ["[", ""]) {
    const broken = { ...cost, articleIntent: { match, priority: 100 } };
    assert.equal(pick("Difyの費用", "", [startup, broken]), startup.slug);
  }
  assert.equal(pick("Difyの費用", "", [{ ...startup, match: "[" }, cost]), cost.slug);
});

test("同点は従来順を保ち、候補配列を変更しない", () => {
  const candidates = [startup, { ...cost, articleIntent: { match: "起動", priority: 10 } }];
  const before = candidates.map((guide) => guide.slug);
  assert.equal(pick("Difyの起動", "", candidates), startup.slug);
  assert.deepEqual(candidates.map((guide) => guide.slug), before);
});
