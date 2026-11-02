import type { ArticleSummary } from "@/lib/articles";

/**
 * Google に見せる記事を選ぶ（サイトでは全記事を読める。noindex でも一覧・内部リンクには残す）。
 * scripts/indexnow.py の is_indexable と同じ基準にしておくこと。
 *
 * 検索に出す条件（手動指定 indexable が最優先。まとめ記事は常に対象）:
 *  1. プレスリリース配信サービスではない
 *  2. 本文が THIN_BODY 文字以上（薄い要約を出さない）
 *  3. 主題がAI・テック（ジャンル ai / tech-gadget、または題名・タグにAI関連語）
 *  4. 日本の読者向けの価値がある（「日本で使える？」判定 verdict、または
 *     「日本・無料・料金・使い方・始め方・使える」の見出し）
 *  5. 同じ発表の重複記事（duplicateOf。scripts/story.py が付ける）や、同じ題名・同じ出典URLの重複のうち代表1本以外ではない
 *
 * 重複記事は noindex にし、canonical を代表の記事に向ける（app/articles/[slug]/page.tsx の canonicalPathOf）。
 */

/** プレスリリースの配信サービス（量が多く、言い換えになりやすい） */
export const WIRE_SOURCES = new Set(["prtimes", "prtimes-all", "atpress", "value-press", "kyodo-prwire", "dreamnews"]);
/** 本文の最低文字数 */
export const THIN_BODY = 1500;
/** 主題として扱うジャンル */
export const FOCUS_GENRES = new Set(["ai", "tech-gadget"]);
/** それ以外のジャンルでもAIの話題なら対象にする */
export const AI_TOPIC =
  /(?<![A-Za-z])AI(?![a-z])|人工知能|生成|LLM|GPT|Gemini|Claude|Copilot|機械学習|エージェント|ロボット|半導体|NVIDIA/i;

function isFocus(article: ArticleSummary): boolean {
  if (FOCUS_GENRES.has(article.genreSlug)) return true;
  return AI_TOPIC.test(`${article.title} ${(article.tags ?? []).join(" ")}`);
}

/** 検索に出さない理由（出すなら null）。判定の説明・集計に使う */
export function noindexReason(article: ArticleSummary): string | null {
  if (typeof article.indexable === "boolean") return article.indexable ? null : "manual";
  if (article.type === "roundup") return null;
  if (WIRE_SOURCES.has(article.channelSlug)) return "wire";
  if (article.bodyLength < THIN_BODY) return "thin";
  if (!isFocus(article)) return "off-focus";
  if (!article.verdict && !article.hasJapanValue) return "no-japan-value";
  // 重複の印は記事 JSON の duplicateOf と、索引を作るときの予備の判定（lib/article-index.mjs の markDuplicates）
  if (article.duplicate || (article.duplicateOf && article.duplicateOf !== article.slug)) return "duplicate";
  return null;
}

export function isIndexable(article: ArticleSummary): boolean {
  return noindexReason(article) === null;
}

/** 記事ページの canonical（重複記事は代表の記事へ） */
export function canonicalPathOf(article: Pick<ArticleSummary, "slug" | "duplicateOf">): string {
  const target = article.duplicateOf && article.duplicateOf !== article.slug ? article.duplicateOf : article.slug;
  return `/articles/${target}`;
}

/** ニュース用サイトマップに載せる期間（Google の仕様は公開から2日） */
export const NEWS_WINDOW_MS = 2 * 86_400_000;
/** 元の発表からこれ以上たって作った記事は「速報」ではないのでニュース用サイトマップに載せない */
export const NEWS_MAX_SOURCE_AGE_MS = 48 * 3_600_000;

/** 元の発表の時刻（web 記事は sourcePublishedAt、動画は投稿時刻 publishedAt） */
export function sourceTimeOf(article: Pick<ArticleSummary, "sourcePublishedAt" | "publishedAt">): number {
  return Date.parse(article.sourcePublishedAt || article.publishedAt || "");
}

/**
 * ニュース用サイトマップに載せないときの理由（載せるなら null）。scripts/indexnow.py の news_reason と同じ。
 * 公開日は最初に載せた時刻（generatedAt。続報や書き直しで変えない）。
 */
export function newsSitemapReason(article: ArticleSummary, now = Date.now()): string | null {
  const reason = noindexReason(article);
  if (reason) return reason;
  if (article.type === "roundup") return "roundup";
  if (article.angleOf) return "angle";
  if (article.late) return "late";
  const published = Date.parse(article.generatedAt || article.publishedAt || "");
  if (!Number.isFinite(published) || now - published >= NEWS_WINDOW_MS) return "old";
  const source = sourceTimeOf(article);
  if (Number.isFinite(source) && published - source > NEWS_MAX_SOURCE_AGE_MS) return "stale-source";
  return null;
}

/** 一覧ページ（タグ・チャンネル・企業・ジャンル）を検索に出す最低本数（検索対象の記事で数える） */
export const MIN_LISTING = 3;

/** 検索対象の記事が少ない一覧は noindex（ページ自体は残す） */
export function isListingIndexable(articles: ArticleSummary[]): boolean {
  let n = 0;
  for (const a of articles) if (isIndexable(a) && ++n >= MIN_LISTING) return true;
  return false;
}

/** 一覧ページの robots 指定（検索に出すなら空） */
export function listingRobots(articles: ArticleSummary[]) {
  return isListingIndexable(articles) ? {} : { robots: { index: false, follow: true } };
}

/** 外部配信・既存のメール等は、検索方針の変更による内容の急変を防ぎ従来基準を保持する。 */
export function isDistributionEligible(article: ArticleSummary): boolean {
  if (typeof article.indexable === "boolean") return article.indexable;
  if (article.sourceType !== "web" || article.type === "roundup") return true;
  if (WIRE_SOURCES.has(article.channelSlug)) return false;
  return article.bodyLength >= 2000;
}
