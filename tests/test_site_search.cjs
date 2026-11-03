const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// サイト内検索の照合（lib/search-match.ts）とシェアの URL（lib/share.ts）。Next.js を起動せずに検査する。
const root = path.resolve(__dirname, "..");

function load(rel) {
  const sourcePath = path.join(root, rel);
  const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const m = new Module(sourcePath, module);
  m.filename = sourcePath;
  m.paths = Module._nodeModulePaths(path.dirname(sourcePath));
  m._compile(compiled, sourcePath);
  return m.exports;
}

const { normalizeForSearch, queryTerms, scoreRow } = load("lib/search-match.ts");
const { shareHref, sharedUrl } = load("lib/share.ts");

const hit = (query, title, rest = "") => scoreRow(queryTerms(query), normalizeForSearch(title), normalizeForSearch(rest));

test("全角・かっこ・空白の違いを無視して当たる", () => {
  assert.ok(hit("ＮＩＳＡ", "新NISAで月3万円") > 0);
  assert.ok(hit("claude code", "「Claude Code」に新機能") > 0);
  assert.ok(hit("gemini", "Googleの生成AI『Gemini』") > 0);
});

test("空白で区切った言葉はすべて含むものだけ（AND）", () => {
  assert.ok(hit("ChatGPT 料金", "ChatGPTの料金が改定") > 0);
  assert.equal(hit("ChatGPT 料金", "ChatGPTに新機能"), 0);
});

test("会社名はカタカナでも英字でも当たる", () => {
  assert.ok(hit("マイクロソフト", "MicrosoftのCopilot") > 0);
  assert.ok(hit("google", "グーグルが発表") > 0);
});

test("カタカナをひらがなに寄せない（短い言葉の外れを出さない）", () => {
  assert.equal(hit("メタ", "新しい制度を始めた"), 0);
});

test("題名に入っている記事を要約だけの記事より上にする", () => {
  assert.ok(hit("NISA", "新NISAの使い方") > hit("NISA", "投資の話", "NISAにも触れます"));
});

test("シェアの URL に utm を付け、各 SNS の共有画面を使う", () => {
  const url = "https://ai-tech-express.jp/articles/sample";
  assert.equal(sharedUrl(url, "share_copy"), `${url}?utm_source=share_copy&utm_medium=social`);
  const x = new URL(shareHref("x", url, "見出し"));
  assert.equal(x.origin + x.pathname, "https://x.com/intent/post");
  assert.equal(x.searchParams.get("url"), `${url}?utm_source=share_x&utm_medium=social`);
  assert.equal(x.searchParams.get("text"), "見出し");
  assert.match(new URL(shareHref("line", url, "t")).searchParams.get("url"), /utm_source=share_line&utm_medium=social$/);
  assert.match(new URL(shareHref("bluesky", url, "t")).searchParams.get("text"), /utm_source=share_bluesky/);
  assert.match(new URL(shareHref("threads", url, "t")).searchParams.get("text"), /utm_source=share_threads/);
  assert.match(new URL(shareHref("facebook", url, "t")).searchParams.get("u"), /utm_source=share_facebook/);
});
