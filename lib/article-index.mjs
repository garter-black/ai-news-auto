// 記事一覧用の軽い索引（content/index.json）を作る処理。
// ビルド前の scripts/build_index.mjs と、開発時の lib/articles.ts の両方から使うので、
// 依存なしの素の JavaScript にしている（TypeScript の型は lib/articles.ts の ArticleSummary）。
//
// 一覧・関連記事・サイトマップ・カードは全記事の本文を必要としない。本文を含む全 JSON を
// すべてのサーバー関数に同梱するとデプロイの容量が膨らむので、必要な項目だけを索引に入れ、
// 本文は記事ページとカバー画像だけが1本ずつ読む。

import fs from "node:fs";
import path from "node:path";

export const INDEX_VERSION = 5;

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

/**
 * 読者のブラウザに直接読ませてよい画像か。自前の配信（/img/・/cover/）と YouTube のサムネイルだけ。
 * ほかのサイトの画像を直リンクすると、相手の参照元チェック・削除・URL 変更で壊れた画像アイコンになる。
 * 公式発表の写真は scripts/images.py が自前の KV（/img/）に保存してから使う決まりなので、
 * まだ保存されていない外部の URL は空にして、独自カバー（/cover/<slug>）・代替表示に回す（2026-11-03）
 */
const SERVABLE_THUMB = /^(?:https:\/\/ai-tech-express\.jp)?\/(?:img|cover)\/|^https:\/\/(?:i\.ytimg\.com|img\.youtube\.com)\//;
export function servableThumb(url) {
  return typeof url === "string" && SERVABLE_THUMB.test(url) ? url : "";
}

/** 欠けている項目の既定値（lib/articles.ts の normalize と同じ） */
export function withDefaults(parsed) {
  return {
    ...parsed,
    thumbnail: servableThumb(parsed.thumbnail),
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
    sourcePublishedAt: a.sourcePublishedAt,
    indexable: a.indexable,
    duplicateOf: a.duplicateOf,
    angleOf: a.angleOf,
    late: a.late,
    videoId: a.videoId,
    videoUrl: a.videoUrl,
    publishedAt: a.publishedAt,
    generatedAt: a.generatedAt,
    updatedAt: a.updatedAt,
    relatedSlugs: a.relatedSlugs,
    thumbnail: a.thumbnail,
    // 公式発表の写真の出典（トップの大きな写真の下に出す）。ほかの写真の出典は記事ページだけで出す
    imageCredit:
      a.imageCredit?.kind === "official"
        ? { kind: "official", org: a.imageCredit.org, via: a.imageCredit.via, provider: a.imageCredit.provider, imageKind: a.imageCredit.imageKind }
        : a.imageCredit?.kind === "figure"
          ? { kind: "figure", org: a.imageCredit.org }
          : undefined,
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

/** 出典 URL の比較用（クエリ・末尾の / を落とす。scripts/indexnow.py と同じ） */
function urlKey(url) {
  return String(url || "").trim().toLowerCase().replace(/[?#].*$/, "").replace(/\/+$/, "");
}

/**
 * 重複の印（duplicate）を付ける。
 * 1. 記事 JSON の duplicateOf（scripts/story.py が同じ発表の記事を見分けて付ける。主な判定はこちら）
 * 2. 予備: 同じ題名・同じ出典URLの記事のうち代表以外（本文の長い方、同じなら先に載った方が代表）。
 *    duplicateOf のある記事・意図して角度を変えた解説（angleOf）は予備の判定に入れない
 */
export function markDuplicates(items) {
  for (const a of items) if (a.duplicateOf && a.duplicateOf !== a.slug) a.duplicate = true;
  const keep = new Set();
  const ordered = items
    .filter((a) => a.type !== "roundup" && !a.duplicate && !a.angleOf)
    .sort((a, b) => b.bodyLength - a.bodyLength || String(a.generatedAt).localeCompare(String(b.generatedAt)));
  for (const a of ordered) {
    const keys = [`t:${titleKey(a.title)}`, ...(a.sourceUrl ? [`u:${urlKey(a.sourceUrl)}`] : [])];
    if (keys.some((k) => keep.has(k))) a.duplicate = true;
    else for (const k of keys) keep.add(k);
  }
}
