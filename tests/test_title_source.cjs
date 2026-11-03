const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 記事の題名は1つだけ（lib/headline.ts の articleTitle。台帳 A29）。
// 見出し h1・<title>・og:title・twitter:title・OG 画像（/cover）・SNS カード（/card）・ニュース用サイトマップ・RSS が同じ題名を使うかを確かめる。
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

const { articleTitle } = load("lib/headline.ts");
const { coverPath } = load("lib/cover-path.ts");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");

test("題名の正本は記事の title。検索向けの seoTitle は使わない（title が空のときだけ予備）", () => {
  const article = {
    title: "OpenAI、セキュリティのバグ報奨金を増額　重大な欠陥は最大3万ドルに",
    seoTitle: "OpenAIバグ報奨金とは？重大な欠陥は最大3万ドルに増額",
  };
  assert.equal(articleTitle(article), article.title);
  assert.equal(articleTitle({ title: "", seoTitle: "予備の題名" }), "予備の題名");
});

test("説明つきの社名（「ChatGPTのOpenAIが」）は外す", () => {
  assert.equal(articleTitle({ title: "ChatGPTのOpenAIが電力対策「Grid Protectors」" }), "OpenAIが電力対策「Grid Protectors」");
  // 関係を表す言葉は外さない
  assert.equal(articleTitle({ title: "競合のGoogleが新モデル" }), "競合のGoogleが新モデル");
});

test("OG 画像の URL は題名を直すと変わる（ブラウザ・SNS・エッジに古い題名の画像を使わせない）", () => {
  const a = coverPath({ slug: "x", title: "古い題名" });
  const b = coverPath({ slug: "x", title: "新しい題名" });
  assert.notEqual(a, b);
  assert.match(a, /^\/cover\/x\?v=\d+-[0-9a-z]+$/);
  assert.match(coverPath({ slug: "x" }), /^\/cover\/x\?v=\d+$/);
});

test("記事ページ・画像・サイトマップ・RSS は articleTitle を通す（seoTitle を題名に出さない）", () => {
  const page = read("app/articles/[slug]/page.tsx");
  const meta = page.slice(page.indexOf("export async function generateMetadata"), page.indexOf("export default async function"));
  assert.match(meta, /const title = articleTitle\(article\)/);
  assert.doesNotMatch(meta, /article\.seoTitle/);
  assert.doesNotMatch(meta, /title: article\.title/);
  assert.match(page, /<Highlight text=\{articleTitle\(article\)\} \/>/);
  assert.match(page, /headline: articleTitle\(article\)/);
  assert.match(read("app/cover/[slug]/route.tsx"), /const title = articleTitle\(article\)/);
  assert.match(read("app/card/[slug]/route.tsx"), /const title = articleTitle\(article\)/);
  assert.match(read("lib/card-designs.tsx"), /const title = articleTitle\(article\)/);
  assert.match(read("app/news-sitemap.xml/route.ts"), /articleTitle\(a\)/);
  assert.match(read("app/feed.xml/route.ts"), /articleTitle\(article\)/);
});

test("一覧・部品にも「seoTitle || title」で題名を出す所が残っていない", () => {
  const offenders = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(path.join(root, dir))) {
      const rel = path.join(dir, name);
      const stat = fs.statSync(path.join(root, rel));
      if (stat.isDirectory()) walk(rel);
      else if (/\.(tsx?|mjs)$/.test(name) && /\.seoTitle \|\| [A-Za-z_.]*\.title/.test(read(rel))) offenders.push(rel);
    }
  };
  for (const dir of ["app", "components", "lib"]) walk(dir);
  assert.deepEqual(offenders, []);
});
