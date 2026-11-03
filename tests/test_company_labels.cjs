const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 企業の札（lib/companies.ts の companyOf）。見出しの主語の会社を先に見る（台帳 A30）。
// 例は 2026-11-03 の点検で見つかった本物の付け間違い（Qodo のプラグインの記事に「OpenAI」の札など）。
const root = path.resolve(__dirname, "..");
const cache = new Map();
function load(file) {
  if (cache.has(file)) return cache.get(file).exports;
  const m = new Module(file);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  cache.set(file, m);
  const req = m.require.bind(m);
  m.require = (id) => {
    if (id.startsWith("@/") || id.startsWith(".")) {
      const base = id.startsWith("@/") ? path.join(root, id.slice(2)) : path.resolve(path.dirname(file), id);
      for (const ext of ["", ".ts", ".tsx", ".json"]) {
        const p = base + ext;
        if (fs.existsSync(p) && fs.statSync(p).isFile()) return p.endsWith(".json") ? JSON.parse(fs.readFileSync(p, "utf8")) : load(p);
      }
    }
    return req(id);
  };
  m._compile(
    ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText,
    file,
  );
  return m.exports;
}

const { companyOf, subjectOf } = load(path.join(root, "lib/companies.ts"));
let n = 0;
const art = (title, extra = {}) => ({ slug: `t${n++}`, title, tags: [], description: "", channelSlug: "x", sourceType: "web", sourceName: "", ...extra });

test("主語の会社が先（発信元が OpenAI の公式ブログでも、Qodo の発表は OpenAI にしない）", () => {
  assert.equal(companyOf(art("Qodo、AIコーディングツール「Codex」向けプラグインを公開　書き終えた直後にコードを点検", { sourceName: "OpenAI公式ブログ" })).slug, "other");
  assert.equal(companyOf(art("ソフトバンクが「Google Pixel 11 Pro」の料理撮影術を公開", { sourceName: "ソフトバンクニュース" })).slug, "other");
  assert.equal(companyOf(art("Uber、全社員3万人超に「ChatGPT Work」を展開", { sourceName: "OpenAI公式ブログ" })).slug, "other");
  assert.equal(companyOf(art("NVIDIA社員、ローカルで動く音声エージェント「MonsterHouse」の仕組みを公開", { sourceName: "Hugging Face公式ブログ" })).slug, "nvidia");
  assert.equal(companyOf(art("OpenAI、セキュリティのバグ報奨金を増額", { sourceName: "OpenAI公式ブログ" })).slug, "openai");
});

test("AWS・Apple・サムスン・Salesforce は「その他」にまとめない", () => {
  assert.equal(companyOf(art("AWS、Bedrockの構造化出力を一般提供", { sourceName: "AWS ブログ（日本）" })).slug, "amazon");
  assert.equal(companyOf(art("Amazon Bedrockで「GPT-6.1 Terra」の提供開始　料金は前モデルの半分以下", { sourceName: "AWS ブログ（日本）" })).slug, "amazon");
  assert.equal(companyOf(art("Apple、新しい「Siri AI」を日本語で提供開始", { sourceName: "Apple Newsroom（日本）" })).slug, "apple");
  assert.equal(companyOf(art("サムスン、100µm未満のLEDを使う「Micro RGB TV」を発表")).slug, "samsung");
  assert.equal(companyOf(art("Salesforce、音声AI開発の米新興Crestaを買収へ")).slug, "salesforce");
  assert.equal(companyOf(art("Salesforce、新基盤「AIforce」を発表　ClaudeやSlackから業務アプリを直接操作")).slug, "salesforce");
});

test("主語が会社でない見出しは、今までどおり見出し・発信元から決める", () => {
  assert.equal(subjectOf("AIエージェント実用化の3技術を整理　長期記憶「Mem0」"), "");
  assert.equal(companyOf(art("AIエージェントの長期記憶フレームワーク「Mem0」の内部構造を解説", { sourceName: "Hugging Face公式ブログ" })).slug, "huggingface");
});

test("記事 JSON の companySlug（Haiku で確かめて直したもの）が最優先", () => {
  assert.equal(companyOf(art("Qodo、Codex向けプラグイン", { companySlug: "openai" })).slug, "openai");
  assert.equal(companyOf(art("OpenAIの話", { companySlug: "other" })).slug, "other");
});
