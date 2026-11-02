// 記事一覧用の軽い索引（content/index.json）を作る処理。
// ビルド前の scripts/build_index.mjs と、開発時の lib/articles.ts の両方から使うので、
// 依存なしの素の JavaScript にしている（TypeScript の型は lib/articles.ts の ArticleSummary）。
//
// 一覧・関連記事・サイトマップ・カードは全記事の本文を必要としない。本文を含む全 JSON を
// すべてのサーバー関数に同梱するとデプロイの容量が膨らむので、必要な項目だけを索引に入れ、
// 本文は記事ページとカバー画像だけが1本ずつ読む。

import fs from "node:fs";
import path from "node:path";

export const INDEX_VERSION = 3;

/** 記事 JSON として最低限そろっているか（lib/articles.ts の isArticle と同じ基準） */
export function isArticleLike(value) {
  if (typeof value !== "object" || value === null) return false;
  return (
    typeof value.slug === "string" &&
    typeof value.title === "string" &&
    typeof value.body === "string" &&
    typeof value.videoId === "string"
  );
}

/** 欠けている項目の既定値（lib/articles.ts の normalize と同じ） */
export function withDefaults(parsed) {
  return {
    ...parsed,
    keywords: parsed.keywords ?? [],
    tags: parsed.tags ?? [],
    highlights: parsed.highlights ?? [],
    timetable: parsed.timetable ?? [],
    affiliates: parsed.affiliates ?? [],
    sources: parsed.sources ?? [],
    lede: parsed.lede ?? "",
    genre: parsed.genre ?? "AI・テクノロジー",
    genreSlug: parsed.genreSlug ?? "ai",
    disclaimer: parsed.disclaimer ?? "",
  };
}

/** 「かんたん」版の「ひとことで言うと」（または「概要」）段落（無ければ空。表示側 lib/general.ts の easyGist がリード文に戻す） */
export function easyGistOf(easy) {
  const match = (easy ?? "").match(/##\s*(?:ひとことで言うと|概要)\s*\n+([\s\S]*?)(?=\n##\s|$)/);
  return (match?.[1] ?? "").trim().replace(/\s*\n+\s*/g, "");
}

/** 本文から計算する、一覧で使う値 */
export function derivedOf(article) {
  return {
    bodyLength: article.body.length,
    hasEasy: Boolean(article.easy && article.easy.trim()),
    // 「日本で使える？無料？料金・使い方」の見出しがあるか（検索に出す条件。indexnow.py と同じ）
    hasJapanValue: /^#{2,3}.*(日本|無料|使い方|料金|始め方|使える)/m.test(article.body),
    easyGist: easyGistOf(article.easy),
    sourceCount: Array.isArray(article.sources) ? article.sources.length : 0,
    affiliateNames: Array.isArray(article.affiliates) ? article.affiliates.map((a) => a.name).filter(Boolean) : [],
  };
}

/** 一覧で使う項目だけを抜き出す（ArticleSummary） */
export function summarize(article) {
  const a = withDefaults(article);
  return {
    slug: a.slug,
    type: a.type,
    roundupSlug: a.roundupSlug,
    roundupTitle: a.roundupTitle,
    title: a.title,
    seoTitle: a.seoTitle,
    lede: a.lede,
    description: a.description,
    keywords: a.keywords,
    tags: a.tags,
    // 一覧（ストーリー）では先頭3つまでしか使わない
    highlights: a.highlights.slice(0, 3),
    verdict: a.verdict ?? null,
    channel: a.channel,
    channelId: a.channelId,
    channelSlug: a.channelSlug,
    genre: a.genre,
    genreSlug: a.genreSlug,
    sourceType: a.sourceType,
    sourceUrl: a.sourceUrl,
    sourceName: a.sourceName,
    indexable: a.indexable,
    videoId: a.videoId,
    videoUrl: a.videoUrl,
    publishedAt: a.publishedAt,
    generatedAt: a.generatedAt,
    updatedAt: a.updatedAt,
    relatedSlugs: a.relatedSlugs,
    thumbnail: a.thumbnail,
    speedMinutes: a.speedMinutes,
    ...derivedOf(a),
  };
}

/** サイソクに載った順（新しい順） */
export function byNewest(a, b) {
  return (
    new Date(b.generatedAt || b.publishedAt).getTime() - new Date(a.generatedAt || a.publishedAt).getTime()
  );
}

/** content/articles/*.json を全部読んで索引を作る。壊れた JSON は1本ずつ飛ばす */
export function buildIndex(articlesDir) {
  if (!fs.existsSync(/* turbopackIgnore: true */ articlesDir)) return [];
  const items = [];
  for (const file of fs.readdirSync(/* turbopackIgnore: true */ articlesDir)) {
    if (!file.endsWith(".json")) continue;
    try {
      const parsed = JSON.parse(fs.readFileSync(/* turbopackIgnore: true */ path.join(articlesDir, file), "utf8"));
      if (!isArticleLike(parsed)) {
        console.warn(`[articles] skip (schema mismatch): ${file}`);
        continue;
      }
      items.push(summarize(parsed));
    } catch (error) {
      console.warn(`[articles] skip (invalid JSON): ${file}`, error);
    }
  }
  markDuplicates(items);
  return items.sort(byNewest);
}

/** 重複の判定に使う題名（記号・空白を除く。scripts/indexnow.py と同じ） */
function titleKey(title) {
  return String(title || "").replace(/[\s「」『』【】()（）・、。！？!?:：|｜\-—〜~"'“”]/g, "").toLowerCase();
}

/** 同じ題名・出典URLの重複のうち代表以外に duplicate を付ける（本文の長い方、同じなら先に載った方が代表） */
export function markDuplicates(items) {
  const keep = new Set();
  const ordered = items
    .filter((a) => a.type !== "roundup")
    .sort((a, b) => b.bodyLength - a.bodyLength || String(a.generatedAt).localeCompare(String(b.generatedAt)));
  for (const a of ordered) {
    const keys = [`t:${titleKey(a.title)}`, ...(a.sourceUrl ? [`u:${a.sourceUrl}`] : [])];
    if (keys.some((k) => keep.has(k))) a.duplicate = true;
    else for (const k of keys) keep.add(k);
  }
}
