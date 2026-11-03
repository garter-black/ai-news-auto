import type { ArticleSummary } from "@/lib/articles";
import { getAllHowtos, type HowtoGuide } from "@/lib/howto";
import { getAllGuides, guideHref } from "@/lib/guides";
import { chooseGuideForArticle } from "@/lib/choose";

/**
 * ニュース記事から、同じ製品の「料金」「使い方」の解説（/guides/<slug>）・製品ガイド・選び方ガイド（/choose）への固定の案内。
 * 「3秒でわかる要点」のすぐ下と、本文の料金の章の下（料金ガイドがあるときだけ）に出す。
 * 製品は記事の見出し・短い題名で照合する（本文のついでの言及では出さない）。例: Claude Ultra の記事 → Claudeの料金ガイド。
 */
export type GuideLink = { href: string; label: string; kind: "pricing" | "howto" | "guide" | "choose" };

const PRICING_INTENT = /料金|無料|費用|クレジット/;

function test(pattern: string | undefined, text: string): boolean {
  if (!pattern) return false;
  try {
    return new RegExp(pattern).test(text);
  } catch {
    return false;
  }
}

function label(guide: HowtoGuide): string {
  return guide.title;
}

/** 記事に合う案内（料金ガイド → 使い方ガイド → 製品ガイド → 選び方ガイドの順。最大2本） */
export function guideLinksForArticle(article: ArticleSummary, howtos: HowtoGuide[] = getAllHowtos()): GuideLink[] {
  if (article.type === "roundup") return [];
  const text = `${article.title} ${article.seoTitle ?? ""}`;
  const matched = howtos.filter((guide) => test(guide.match, text));
  // 同じ製品（tool）のガイドだけにそろえる（最初に当たった製品）。別の製品のガイドを混ぜない
  const tool = matched[0]?.tool;
  const sameTool = matched.filter((guide) => guide.tool === tool);
  const links: GuideLink[] = [];
  const pricing = sameTool.find((guide) => PRICING_INTENT.test(guide.intent));
  if (pricing) links.push({ href: `/guides/${pricing.slug}`, label: label(pricing), kind: "pricing" });
  const howto =
    sameTool.find((guide) => /使い方|始め方/.test(guide.intent) && guide.slug !== pricing?.slug) ??
    sameTool.find((guide) => guide.slug !== pricing?.slug);
  if (howto) links.push({ href: `/guides/${howto.slug}`, label: label(howto), kind: "howto" });
  if (links.length < 2) {
    const product = getAllGuides().find((guide) => test(guide.match, text));
    if (product) links.push({ href: guideHref(product.slug), label: `${product.name}とは？できること・料金・使い方`, kind: "guide" });
  }
  if (links.length < 2) {
    const choose = chooseGuideForArticle(article);
    if (choose) links.push({ href: `/choose/${choose.slug}`, label: choose.articleLabel, kind: "choose" });
  }
  return links.slice(0, 2);
}

/** 本文の料金の章（「## 料金は？」など）の終わりに、料金ガイドへの1行を差し込む。章が無ければそのまま */
export function insertAfterPricingSection(markdown: string, link: GuideLink | undefined): string {
  if (!link || link.kind !== "pricing") return markdown;
  const lines = markdown.split("\n");
  const start = lines.findIndex((line) => /^##\s+.*(料金|価格|値段|いくら|プラン)/.test(line));
  if (start < 0) return markdown;
  let end = lines.findIndex((line, index) => index > start && /^##\s/.test(line));
  if (end < 0) end = lines.length;
  const note = `<p class="guide-link-note">料金の詳しい比較は <a href="${link.href}">${escapeHtml(link.label)}</a> へ</p>`;
  return [...lines.slice(0, end), "", note, "", ...lines.slice(end)].join("\n");
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
