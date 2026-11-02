import { getAllArticles, type ArticleSummary } from "@/lib/articles";

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
 *  5. 同じ題名・同じ出典URLの重複記事のうち、代表1本以外ではない
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

/** 重複の判定に使う題名（記号・空白を除く） */
function titleKey(title: string): string {
  return title.replace(/[\s「」『』【】()（）・、。！？!?:：|｜\-—〜~"'“”]/g, "").toLowerCase();
}

let duplicates: Set<string> | null = null;
/** 代表でない重複記事の slug（本文の長い方を代表、同じなら先に載った方） */
export function duplicateSlugs(): Set<string> {
  if (duplicates) return duplicates;
  const keep = new Set<string>();
  const out = new Set<string>();
  const ordered = getAllArticles()
    .filter((a) => a.type !== "roundup")
    .sort((a, b) => b.bodyLength - a.bodyLength || (a.generatedAt < b.generatedAt ? -1 : 1));
  for (const a of ordered) {
    const keys = [`t:${titleKey(a.title)}`, ...(a.sourceUrl ? [`u:${a.sourceUrl}`] : [])];
    if (keys.some((k) => keep.has(k))) out.add(a.slug);
    else for (const k of keys) keep.add(k);
  }
  duplicates = out;
  return out;
}

/** 検索に出さない理由（出すなら null）。判定の説明・集計に使う */
export function noindexReason(article: ArticleSummary): string | null {
  if (typeof article.indexable === "boolean") return article.indexable ? null : "manual";
  if (article.type === "roundup") return null;
  if (WIRE_SOURCES.has(article.channelSlug)) return "wire";
  if (article.bodyLength < THIN_BODY) return "thin";
  if (!isFocus(article)) return "off-focus";
  if (!article.verdict && !article.hasJapanValue) return "no-japan-value";
  if (duplicateSlugs().has(article.slug)) return "duplicate";
  return null;
}

export function isIndexable(article: ArticleSummary): boolean {
  return noindexReason(article) === null;
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
