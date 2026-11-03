const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");
const { marked } = require("marked");

// 本文の生のURLの自動リンク（lib/autolink.ts）。日本語・全角記号・空白の手前で止める。
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/autolink.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded.paths = Module._nodeModulePaths(root);
loaded._compile(compiled, sourcePath);
const { matchBareUrl, bareUrlToken } = loaded.exports;

marked.setOptions({ gfm: true, breaks: false });
marked.use({ tokenizer: { url: (src) => bareUrlToken(src) } });
const render = (md) => marked.parse(md).trim();
const links = (md) => [...render(md).matchAll(/<a href="([^"]*)">([^<]*)<\/a>/g)].map((m) => [m[1], m[2]]);

test("日本語の文に直接続くURLは、日本語の手前で止める（リンク文字とリンク先の両方）", () => {
  const url = "https://example.com/a/b";
  assert.deepEqual(links(`「…${url}である。」`), [[url, url]]);
  assert.deepEqual(links(`公式サイトのURLは${url}である。`), [[url, url]]);
  const jpcert = "https://www.jpcert.or.jp/wr/2026/wr261028.html#1";
  assert.deepEqual(links(`公式サイトのURLは${jpcert}である。`), [[jpcert, jpcert]]);
  assert.equal(render(`URLは${url}である。`), `<p>URLは<a href="${url}">${url}</a>である。</p>`);
});

test("全角のかっこ・句読点・カギかっこで止める", () => {
  assert.deepEqual(links("公式ページ（https://claude.ai/）にログインします。"), [["https://claude.ai/", "https://claude.ai/"]]);
  assert.deepEqual(links("詳細はhttps://example.com/x、またはhttps://example.com/y。"), [
    ["https://example.com/x", "https://example.com/x"],
    ["https://example.com/y", "https://example.com/y"],
  ]);
  assert.deepEqual(links("「https://example.com/q?a=1&b=2」を開く"), [["https://example.com/q?a=1&b=2", "https://example.com/q?a=1&amp;b=2"]]);
});

test("末尾の半角の句読点と、対になっていない ) ] は外す。対になったかっこは残す", () => {
  assert.equal(matchBareUrl("https://example.com/a/b. Next"), "https://example.com/a/b");
  assert.equal(matchBareUrl("https://example.com/a/b, x"), "https://example.com/a/b");
  assert.equal(matchBareUrl("https://example.com/a/b)."), "https://example.com/a/b");
  assert.equal(matchBareUrl("https://example.com/a]"), "https://example.com/a");
  assert.equal(matchBareUrl("https://en.wikipedia.org/wiki/Foo_(bar))"), "https://en.wikipedia.org/wiki/Foo_(bar)");
  assert.equal(matchBareUrl("https://example.com/a?x=1#frag。"), "https://example.com/a?x=1#frag");
  assert.equal(matchBareUrl("https://example.com/a b"), "https://example.com/a");
  assert.equal(matchBareUrl("www.example.com/aです"), "www.example.com/a");
  assert.equal(matchBareUrl("https://。"), null);
  assert.equal(matchBareUrl("ただの文"), null);
  assert.deepEqual(links("(see https://example.com/a)"), [["https://example.com/a", "https://example.com/a"]]);
});

test("ふつうのMarkdownリンクとメールアドレスは今までどおり", () => {
  assert.deepEqual(links("[公式サイト](https://example.com/a/b)である。"), [["https://example.com/a/b", "公式サイト"]]);
  assert.deepEqual(links("連絡先はinfo@example.comです"), [["mailto:info@example.com", "info@example.com"]]);
  assert.deepEqual(links("mail: info@example.com"), [["mailto:info@example.com", "info@example.com"]]);
  assert.deepEqual(links("`https://example.com/aです`"), []); // コードの中はリンクにしない
});
