// ビルド前に content/index.json（記事一覧用の軽い索引）を作る。npm run build から呼ばれる。
// 仕組みは lib/article-index.mjs を参照。生成物なので git には入れない（.gitignore）。
// あわせてサイト内検索の索引（public/search-index/<n>.json。lib/search-index.mjs）も作る。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildIndex, INDEX_VERSION } from "../lib/article-index.mjs";
import { createRequire } from "node:module";
import { searchChunks, searchExclusion } from "../lib/search-index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const articles = buildIndex(path.join(root, "content", "articles"));
const out = path.join(root, "content", "index.json");
fs.writeFileSync(out, JSON.stringify({ version: INDEX_VERSION, articles }));
console.log(`[build_index] ${articles.length} articles -> content/index.json (${(fs.statSync(out).size / 1024).toFixed(0)} KB)`);

const searchDir = path.join(root, "public", "search-index");
fs.rmSync(searchDir, { recursive: true, force: true });
fs.mkdirSync(searchDir, { recursive: true });
// 検索に出さない記事（lib/indexing.ts の noindexReason）と取り下げた記事は索引に入れない（台帳 W24）。
// indexing.ts は TypeScript なので、テストと同じく typescript で CommonJS に直して読む
function loadNoindexReason() {
  const require = createRequire(import.meta.url);
  const ts = require("typescript");
  const Module = require("node:module");
  const file = path.join(root, "lib", "indexing.ts");
  const compiled = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, resolveJsonModule: true },
  }).outputText;
  const m = new Module(file);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  m._compile(compiled, file);
  return m.exports.noindexReason;
}
const retired = (() => {
  try {
    return Object.keys(JSON.parse(fs.readFileSync(path.join(root, "content", "retired.json"), "utf8")).articles ?? {});
  } catch {
    return [];
  }
})();
const exclude = searchExclusion(loadNoindexReason(), retired);
const chunks = searchChunks(articles, exclude);
let bytes = 0;
chunks.forEach((rows, n) => {
  const file = path.join(searchDir, `${n}.json`);
  fs.writeFileSync(file, JSON.stringify(rows));
  bytes += fs.statSync(file).size;
});
console.log(`[build_index] search: ${chunks.reduce((s, c) => s + c.length, 0)} articles (excluded ${articles.filter(exclude).length} noindex/retired) in ${chunks.length} files -> public/search-index (${(bytes / 1024).toFixed(0)} KB)`);
