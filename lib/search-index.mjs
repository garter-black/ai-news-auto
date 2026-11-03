// サイト内検索（/search）の索引。ビルド前に scripts/build_index.mjs が public/search-index/<n>.json に書き出し、
// 検索ページ（app/search）が同じ関数で「何個に分けたか・版」を計算して読み込む。
// 依存なしの素の JavaScript（lib/article-index.mjs と同じ理由）。
//
// 1件は配列 [slug, 題名, 要約, タグ（空白区切り）, ジャンルの slug, 日付 YYYY-MM-DD, サムネイル] にして小さくする。
// 新しい順に最大 SEARCH_LIMIT 本。CHUNK 本ずつに分け、検索ページは新しい方から順に読む（最初の1つで結果を出す）。

export const SEARCH_LIMIT = 1000;
export const SEARCH_CHUNK = 250;
const SUMMARY_MAX = 90;

/**
 * 索引から外す記事の判定を作る（台帳 W24）。取り下げた記事（content/retired.json）と、検索に出さない記事（lib/indexing.ts の noindexReason。
 * 薄い・主題外・重複など）は、サイト内検索にも出さない。ビルド前の scripts/build_index.mjs と検索ページ（app/search/page.tsx）が
 * 同じ判定を使う（ファイルの数がずれないように）。noindexReason を渡さないときは重複だけを外す
 */
export function searchExclusion(noindexReason, retiredSlugs = []) {
  const retired = new Set(retiredSlugs);
  return (a) => retired.has(a.slug) || (noindexReason ? noindexReason(a) !== null : false);
}

/** 索引に入れる記事（重複の代表でないもの・地震の速報・取り下げ・検索に出さない記事は入れない）を新しい順に */
export function searchArticles(summaries, exclude = () => false) {
  return summaries.filter((a) => !a.duplicate && !a.disaster && !exclude(a)).slice(0, SEARCH_LIMIT);
}

/** 日本時間の日付 YYYY-MM-DD（読めなければ空） */
function jstDate(iso) {
  const t = Date.parse(iso || "");
  return Number.isNaN(t) ? "" : new Date(t + 9 * 3600 * 1000).toISOString().slice(0, 10);
}

/** 1件分の配列 */
export function searchRow(a) {
  const summary = String(a.description || a.lede || "").replace(/\s+/g, " ").trim();
  return [
    a.slug,
    a.title,
    summary.length > SUMMARY_MAX ? `${summary.slice(0, SUMMARY_MAX)}…` : summary,
    (a.tags || []).join(" "),
    a.genreSlug || "",
    jstDate(a.generatedAt || a.publishedAt),
    a.thumbnail || "",
  ];
}

/** CHUNK 本ずつに分けた配列の配列 */
export function searchChunks(summaries, exclude) {
  const rows = searchArticles(summaries, exclude).map(searchRow);
  const chunks = [];
  for (let i = 0; i < rows.length; i += SEARCH_CHUNK) chunks.push(rows.slice(i, i + SEARCH_CHUNK));
  return chunks;
}

/** 索引の版（記事が増えるたびに変わる。ブラウザのキャッシュを使い分けるためにファイルの URL に付ける） */
export function searchVersion(summaries, exclude) {
  const items = searchArticles(summaries, exclude);
  const key = `${items.length}:${items[0]?.slug ?? ""}:${items[0]?.generatedAt ?? ""}:${items.at(-1)?.slug ?? ""}`;
  let h = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    h ^= key.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(36);
}
