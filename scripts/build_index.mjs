// ビルド前に content/index.json（記事一覧用の軽い索引）を作る。npm run build から呼ばれる。
// 仕組みは lib/article-index.mjs を参照。生成物なので git には入れない（.gitignore）。
// あわせてサイト内検索の索引（public/search-index/<n>.json。lib/search-index.mjs）も作る。
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildIndex, INDEX_VERSION } from "../lib/article-index.mjs";
import { searchChunks } from "../lib/search-index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const articles = buildIndex(path.join(root, "content", "articles"));
const out = path.join(root, "content", "index.json");
fs.writeFileSync(out, JSON.stringify({ version: INDEX_VERSION, articles }));
console.log(`[build_index] ${articles.length} articles -> content/index.json (${(fs.statSync(out).size / 1024).toFixed(0)} KB)`);

const searchDir = path.join(root, "public", "search-index");
fs.rmSync(searchDir, { recursive: true, force: true });
fs.mkdirSync(searchDir, { recursive: true });
const chunks = searchChunks(articles);
let bytes = 0;
chunks.forEach((rows, n) => {
  const file = path.join(searchDir, `${n}.json`);
  fs.writeFileSync(file, JSON.stringify(rows));
  bytes += fs.statSync(file).size;
});
console.log(`[build_index] search: ${chunks.reduce((s, c) => s + c.length, 0)} articles in ${chunks.length} files -> public/search-index (${(bytes / 1024).toFixed(0)} KB)`);
