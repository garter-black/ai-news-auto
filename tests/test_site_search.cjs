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

const { normalizeForSearch, normalizeTags, queryTerms, rankHits, scoreRow } = load("lib/search-match.ts");
const { shareHref, sharedUrl } = load("lib/share.ts");

const hit = (query, title, rest = "", tags = "") =>
  scoreRow(queryTerms(query), normalizeForSearch(title), normalizeForSearch(`${rest} ${tags}`), normalizeTags(tags));

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

// ---- 台帳 W24: 並び順（関連の強さ → 新しい順）・取り下げ／noindex を出さない・日本語の言い換え ----

test("カタカナの呼び名でも英字の製品名に当たる（「クロード」→Claude など）", () => {
  assert.ok(hit("クロード", "Claude 5の料金が改定") > 0);
  assert.ok(hit("ジェミニ", "Google、Gemini 4を発表") > 0);
  assert.ok(hit("クロードコード", "「Claude Code」に新機能") > 0);
  assert.ok(hit("ディープシーク", "DeepSeek、新モデルを公開") > 0);
  assert.ok(hit("アトラシアン", "Atlassianが受賞") > 0);
  // 逆向き（英字で探してカタカナの見出し）
  assert.ok(hit("claude", "クロードの使い方") > 0);
});

test("半角カナ・全角英字もそろえる", () => {
  assert.ok(hit("ｸﾛｰﾄﾞ", "Claudeの新機能") > 0);
  assert.ok(hit("ＣＬＡＵＤＥ", "Claudeの新機能") > 0);
});

test("言葉の一部に入った呼び名も言い換える（「クロードの料金」）", () => {
  assert.ok(hit("クロード料金", "Claude料金の早見表") > 0);
});

test("強さ: 題名の頭（主語）＞題名＞会社・製品のタグ＞要約だけ", () => {
  const subject = hit("Claude", "Claude、新モデルを公開");
  const inTitle = hit("Claude", "Anthropic、新しいClaudeを公開");
  const inTag = hit("Claude", "Anthropic、新モデルを公開", "", "Anthropic Claude");
  const inSummary = hit("Claude", "Anthropic、新モデルを公開", "Claudeの最新版です");
  assert.ok(subject > inTitle && inTitle > inTag && inTag > inSummary && inSummary > 0);
});

test("同じ強さなら新しい記事が先（order が小さいほど新しい）", () => {
  const ranked = rankHits([
    { item: { order: 5, id: "old-title" }, score: 3 },
    { item: { order: 1, id: "new-summary" }, score: 1 },
    { item: { order: 2, id: "new-title" }, score: 3 },
  ]);
  assert.deepEqual(ranked.map((x) => x.id), ["new-title", "old-title", "new-summary"]);
});

test("取り下げた記事・検索に出さない記事（noindex）は索引に入れない", async () => {
  const { searchArticles, searchExclusion } = await import(path.join(root, "lib/search-index.mjs"));
  const items = [
    { slug: "ok", title: "a" },
    { slug: "thin", title: "b" },
    { slug: "gone", title: "c" },
    { slug: "dup", title: "d", duplicate: true },
  ];
  const exclude = searchExclusion((a) => (a.slug === "thin" ? "thin" : null), ["gone"]);
  assert.deepEqual(searchArticles(items, exclude).map((a) => a.slug), ["ok"]);
  // ビルドの索引と検索ページが同じ判定を使う
  const build = fs.readFileSync(path.join(root, "scripts/build_index.mjs"), "utf8");
  const page = fs.readFileSync(path.join(root, "app/search/page.tsx"), "utf8");
  assert.match(build, /searchExclusion\(loadNoindexReason\(\), retired\)/);
  assert.match(page, /searchExclusion\(noindexReason, retiredSlugs\(\)\)/);
});
