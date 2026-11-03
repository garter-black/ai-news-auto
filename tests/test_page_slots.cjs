const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 同じ広告の枠・メール登録欄は1ページに1つまで（2026-11-03 オーナーのルール W10。本番は scripts/ui_check.mjs と owner_rules_audit が数える）
const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
function load(rel) {
  const file = path.join(root, rel);
  const out = ts.transpileModule(read(rel), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const m = new Module(file, module);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  m._compile(out, file);
  return m.exports;
}

test("記事・/newsletter ではフッターのメール登録欄を出さない", () => {
  const { hasOwnSignup } = load("lib/newsletter-placement.ts");
  for (const p of ["/articles/openai-gpt-6", "/articles/openai-gpt-6/", "/newsletter", "/articles/x?utm_source=threads"]) assert.equal(hasOwnSignup(p), true, p);
  for (const p of ["/", "/articles", "/news", "/guides/chatgpt", "/newsletter/confirm", null]) assert.equal(hasOwnSignup(p), false, String(p));
  const signup = read("components/NewsletterSignup.tsx");
  assert.match(signup, /variant === "footer" && hasOwnSignup\(pathname\)\) return null/);
  assert.match(signup, /data-newsletter-signup=\{variant\}/);
  assert.match(read("app/articles/[slug]/page.tsx"), /<NewsletterSignup variant="article" \/>/);
});

test("かんたん版の中に広告の枠・あわせて読みたいを入れない（通常版と2つになる）", () => {
  const page = read("app/articles/[slug]/page.tsx");
  const start = page.indexOf('<div className="read-easy">');
  assert.ok(start > 0, "かんたん版の囲みが見つからない");
  const easy = page.slice(start, page.indexOf('<div className="read-expert">', start));
  for (const part of ["<RailRanking", "<ReadNextBox", "<AdSlot", "<NewsletterSignup", "<ShopLinks", "<CtaBanner"]) assert.ok(!easy.includes(part), `かんたん版に ${part} がある`);
  // 本文中の商品枠は「後半あり」「後半なし」のどちらか1か所だけ
  const inline = page.match(/\{(!?bodyTail) && inlineOffer && \(\s*<div[^>]*>\s*<RailRanking offer=\{inlineOffer\}/g) ?? [];
  assert.deepEqual(inline.map((m) => m.match(/!?bodyTail/)[0]).sort(), ["!bodyTail", "bodyTail"]);
  assert.equal((page.match(/<RailRanking offer=\{inlineOffer\}/g) ?? []).length, 2);
  assert.equal((page.match(/<AdSlot position="article-middle"/g) ?? []).length, 1);
  assert.equal((page.match(/<AdSlot position="article-top"/g) ?? []).length, 1);
});
