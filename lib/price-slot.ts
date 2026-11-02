import { getComparison, getLadder, type ApiTier, type PlanTier, type PricingProductId } from "@/lib/pricing-ladder";

/**
 * 記事の料金の章に、毎日更新の料金表（components/PriceTable.tsx）を出す場所の印。
 * 記事を作るとき scripts/facts_bundle.py が本文に「### 〇〇のプランと料金（公式の料金と円の目安）」と日付つきの表を差し込み、
 * 記事 JSON に pricingProduct（と同じ段の他社比較 pricingCompare）を残す。ページはその表の場所を最新の PriceTable に差し替える
 * （JSON の表は作った日の控え。検索・RSS・事実確認のために残す）。
 */
export const PRICE_SLOT = "<!--saisoku-price-->";
const MARK = "SAISOKU-PRICE-TABLE-SLOT";
const HEADING = /^###\s.+のプランと料金（公式の料金と円の目安）\s*$/;

export type ArticlePricing = {
  product: PricingProductId;
  compare: { tier: PlanTier | ApiTier; kind: "plan" | "api" } | null;
};

/** 記事に出す料金表（料金データがある製品だけ） */
export function pricingForArticle(article: { pricingProduct?: string; pricingCompare?: string }): ArticlePricing | null {
  const product = article.pricingProduct as PricingProductId | undefined;
  if (!product || !getLadder(product)) return null;
  let compare: ArticlePricing["compare"] = null;
  const [kind, tier] = (article.pricingCompare ?? "").split(":");
  if ((kind === "plan" || kind === "api") && tier) {
    const table = getComparison(tier as PlanTier | ApiTier, kind);
    if (new Set(table.rows.map((r) => r.company)).size >= 2) compare = { tier: tier as PlanTier | ApiTier, kind };
  }
  return { product, compare };
}

/**
 * 本文の差し込み済みの表（見出しの下の表・出典・円の注記の行）を印に置き換える。表が無ければそのまま。
 * withMark=false のときは印も残さない（料金の段の図〔lib/charts.ts の ladder〕がある記事。表は図の下に出すので、本文の場所には何も残さない）
 */
export function withPriceSlot(markdown: string, withMark = true): string {
  const lines = markdown.split("\n");
  const start = lines.findIndex((line) => HEADING.test(line));
  if (start < 0) return markdown;
  let end = start + 1;
  while (end < lines.length && (!lines[end].trim() || /^\s*\|/.test(lines[end]) || /^(出典[:：]|円は)/.test(lines[end].trim()))) end += 1;
  // 見出しも外す（料金表に自分の題名があるので、同じ意味の題名を2つ並べない。目次は ## だけなので影響しない）
  if (!withMark) return [...lines.slice(0, start), ...lines.slice(end)].join("\n");
  return [...lines.slice(0, start), MARK, "", ...lines.slice(end)].join("\n");
}

/** 本文に差し込み済みの料金の表があるか */
export function hasPriceBlock(markdown: string): boolean {
  return markdown.split("\n").some((line) => HEADING.test(line));
}

/** 描いた HTML の印の段落を PRICE_SLOT にする */
export function markPriceSlot(html: string): string {
  return html.replace(new RegExp(`<p>\\s*${MARK}\\s*</p>`), PRICE_SLOT).replace(MARK, "");
}
