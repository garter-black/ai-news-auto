import { getAllChooseGuides } from "@/lib/choose";
import { COMPARE_PUBLIC } from "@/lib/buying-guides";
import { isIndexable, isListingIndexable } from "@/lib/indexing";
import {
  getAllArticles,
  getAllChannels,
  getAllGenres,
  getAllTags,
  getArticlesByChannel,
  getArticlesByGenre,
  getArticlesByTag,
  lastModifiedOf,
  tagSlug,
} from "@/lib/articles";
import { companyCounts, companyOf } from "@/lib/companies";
import { getAllGuides, guideArticles } from "@/lib/guides";
import { getAllHowtos } from "@/lib/howto";
import { getAllBuyingGuides } from "@/lib/buying-guides";
import { getAllEvents } from "@/lib/events";
import { getPricing } from "@/lib/pricing";
import { getAllProducts } from "@/lib/products";
import { getAllWeeks, weekPublishedAt } from "@/lib/weekly";
import { isBroadGenre } from "@/lib/sections";
import { siteConfig } from "@/site.config";

export type SitemapEntry = {
  url: string;
  lastModified: Date;
  changeFrequency: "always" | "hourly" | "daily" | "weekly" | "monthly" | "yearly" | "never";
  priority: number;
};

/** 1ファイルに載せる URL の上限（仕様上は5万。余裕を持たせる） */
export const SITEMAP_CHUNK = 40000;

/** サイトマップに載せる全 URL（記事は全件） */
export function sitemapEntries(): SitemapEntry[] {
  const base = siteConfig.url;
  // 全ジャンルが対象。ただし薄くなりやすい記事は検索に出さないので載せない（lib/indexing.ts）
  const allArticles = getAllArticles();
  const articles = allArticles.filter(isIndexable);
  const liveChannels = new Set(articles.map((a) => a.channelSlug));
  const liveTags = new Set(articles.flatMap((a) => a.tags.map(tagSlug)));
  // 一覧ページの noindex 方針に合わせる。チャンネルは AI 記事があるもの、タグは記事が2本以上。
  // 件数はページと同じく全記事で数え、同じ記事内の重複タグは1本として扱う。
  const aiChannels = new Set(allArticles.filter((a) => a.genreSlug === "ai").map((a) => a.channelSlug));
  const tagArticleCounts = new Map<string, number>();
  for (const article of allArticles) {
    for (const slug of new Set(article.tags.map(tagSlug))) {
      tagArticleCounts.set(slug, (tagArticleCounts.get(slug) ?? 0) + 1);
    }
  }
  const products = getAllProducts();
  const productSlugs = new Set(products.map((p) => p.slug));
  const guides = getAllGuides().filter((guide) => !productSlugs.has(guide.slug));
  const howtos = getAllHowtos();
  const lastModified = articles[0]?.generatedAt
    ? new Date(articles[0].generatedAt)
    : new Date();

  return [
    {
      url: base,
      lastModified,
      changeFrequency: "hourly" as const,
      priority: 1,
    },
    {
      url: `${base}/articles`,
      lastModified,
      changeFrequency: "hourly" as const,
      priority: 0.9,
    },
    ...(allArticles.some((article) => isBroadGenre(article.genreSlug))
      ? [{ url: `${base}/news`, lastModified, changeFrequency: "hourly" as const, priority: 0.9 }]
      : []),
    ...["/about", "/editorial-policy", "/corrections", "/privacy", "/disclaimer", "/contact"].map((path) => ({
      url: `${base}${path}`,
      lastModified,
      changeFrequency: "yearly" as const,
      priority: 0.3,
    })),
    ...articles.map((article) => ({
      url: `${base}/articles/${article.slug}`,
      // 続報を足した記事は最終更新日時
      lastModified: new Date(lastModifiedOf(article)),
      changeFrequency: "weekly" as const,
      priority: 0.8,
    })),
    ...getAllGenres()
      .filter((genre) => (genre.slug === "ai" || isBroadGenre(genre.slug)) && isListingIndexable(getArticlesByGenre(genre.slug)))
      .map((genre) => ({
      url: `${base}/genres/${genre.slug}`,
      lastModified,
      changeFrequency: "hourly" as const,
      priority: 0.9,
    })),
    ...getAllChannels()
      .filter((channel) => liveChannels.has(channel.slug) && aiChannels.has(channel.slug) && isListingIndexable(getArticlesByChannel(channel.slug)))
      .map((channel) => ({
      url: `${base}/channels/${channel.slug}`,
      lastModified,
      changeFrequency: "daily" as const,
      priority: 0.7,
    })),
    { url: `${base}/usable`, lastModified, changeFrequency: "daily" as const, priority: 0.9 },
    // 料金早見表・週まとめ・イベント（被リンクとブックマークを狙う定番ページ）
    { url: `${base}/ai-pricing`, lastModified: new Date(getPricing().updatedAt), changeFrequency: "weekly" as const, priority: 0.9 },
    // 計算ツール（円換算・AIプランの費用・防衛特別所得税）
    ...["/tools", "/tools/usd-jpy", "/tools/ai-plan-cost", "/tools/defense-tax"].map((path) => ({
      url: `${base}${path}`,
      lastModified,
      changeFrequency: "monthly" as const,
      priority: 0.6,
    })),
    // 選び方ガイド（回線・転職など。公式情報で比べた常設ページ）
    ...getAllChooseGuides().map((guide) => ({
      url: `${base}/choose/${guide.slug}`,
      lastModified: new Date(guide.updatedAt),
      changeFrequency: "monthly" as const,
      priority: 0.8,
    })),
    ...(getAllWeeks().length > 0
      ? [{ url: `${base}/weekly`, lastModified: new Date(weekPublishedAt(getAllWeeks()[0])), changeFrequency: "weekly" as const, priority: 0.8 }]
      : []),
    ...getAllWeeks().map((week) => ({
      url: `${base}/weekly/${week.id}`,
      lastModified: new Date(week.generatedAt),
      changeFrequency: "monthly" as const,
      priority: 0.7,
    })),
    { url: `${base}/events`, lastModified, changeFrequency: "daily" as const, priority: 0.8 },
    ...getAllEvents().map((event) => ({
      url: `${base}/events/${event.slug}`,
      lastModified,
      changeFrequency: "daily" as const,
      priority: 0.8,
    })),
    // 製品のまとめページ（「〇〇とは」「料金」の検索の受け皿）
    ...(products.length > 0
      ? [{ url: `${base}/products`, lastModified, changeFrequency: "daily" as const, priority: 0.8 }]
      : []),
    ...products.map((product) => ({
      url: `${base}/products/${product.slug}`,
      lastModified: new Date(product.updatedAt),
      changeFrequency: "daily" as const,
      priority: 0.9,
    })),
    // まとめページへ移した製品ガイドは転送しているので載せない
    ...(guides.length > 0 || howtos.length > 0
      ? [{ url: `${base}/guides`, lastModified, changeFrequency: "daily" as const, priority: 0.8 }]
      : []),
    // 使い方ガイド（長く検索される「使い方・料金・無料でどこまで」）。公式ページで確認し直した日を更新日にする
    ...howtos.map((guide) => ({
      url: `${base}/guides/${guide.slug}`,
      lastModified: new Date(guide.checkedAt > guide.updatedAt ? guide.checkedAt : guide.updatedAt),
      changeFrequency: "weekly" as const,
      priority: 0.9,
    })),
    ...guides.map((guide) => ({
      url: `${base}/guides/${guide.slug}`,
      lastModified: new Date(guideArticles(guide)[0]?.publishedAt ?? lastModified),
      changeFrequency: "daily" as const,
      priority: 0.9,
    })),
    // 比較ガイド（購買意図の強い検索の受け皿）
    ...(COMPARE_PUBLIC && getAllBuyingGuides().length > 0
      ? [{ url: `${base}/compare`, lastModified, changeFrequency: "weekly" as const, priority: 0.8 }]
      : []),
    ...(COMPARE_PUBLIC ? getAllBuyingGuides() : []).map((guide) => ({
      url: `${base}/compare/${guide.slug}`,
      lastModified: new Date(guide.updatedAt),
      changeFrequency: "weekly" as const,
      priority: 0.9,
    })),
    ...companyCounts(articles).filter(({ company }) => isListingIndexable(allArticles.filter((a) => a.genreSlug === "ai" && companyOf(a).slug === company.slug))).map(({ company }) => ({
      url: `${base}/companies/${company.slug}`,
      lastModified,
      changeFrequency: "hourly" as const,
      priority: 0.8,
    })),
    ...getAllTags()
      .filter((tag) => liveTags.has(tag.slug) && (tagArticleCounts.get(tag.slug) ?? 0) >= 2 && isListingIndexable(getArticlesByTag(tag.slug)))
      .map((tag) => ({
      url: `${base}/tags/${encodeURIComponent(tag.slug)}`,
      lastModified,
      changeFrequency: "daily" as const,
      priority: 0.6,
    })),
  ];
}

/** URL 数が上限を超えたら分割する。分割後のファイル数 */
export function sitemapChunkCount(): number {
  return Math.max(1, Math.ceil(sitemapEntries().length / SITEMAP_CHUNK));
}

const escapeXml = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");

export function renderUrlset(entries: SitemapEntry[]): string {
  const urls = entries
    .map(
      (e) =>
        `<url>\n<loc>${escapeXml(e.url)}</loc>\n${Number.isNaN(e.lastModified.getTime()) ? "" : `<lastmod>${e.lastModified.toISOString()}</lastmod>\n`}<changefreq>${e.changeFrequency}</changefreq>\n<priority>${e.priority}</priority>\n</url>`,
    )
    .join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`;
}

export function renderIndex(count: number): string {
  const now = new Date().toISOString();
  const items = Array.from(
    { length: count },
    (_, i) => `<sitemap>\n<loc>${siteConfig.url}/sitemaps/${i}.xml</loc>\n<lastmod>${now}</lastmod>\n</sitemap>`,
  ).join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${items}\n</sitemapindex>\n`;
}

export const XML_HEADERS = { "Content-Type": "application/xml; charset=utf-8" };
