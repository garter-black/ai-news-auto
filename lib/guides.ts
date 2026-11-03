import fs from "@/lib/content-fs";
import path from "node:path";
import { getAllArticles, type ArticleSummary } from "@/lib/articles";
import { supersedesGuide } from "@/lib/products";

/**
 * 製品ガイド（content/guides/*.json）。
 *
 * ニュースは数日で読まれなくなるが、「〇〇とは」「料金」「使い方」は長く検索され続ける。
 * 解説の本文は固定し、関連ニュースの一覧と「最終更新」は記事が増えるたびに自動で更新する。
 */
export type Guide = {
  slug: string;
  name: string;
  companySlug: string;
  /** 関連記事を見つける正規表現（見出しと短い題名に照合） */
  match: string;
  title: string;
  seoTitle: string;
  description: string;
  summary: string[];
  intro: string;
  features: { title: string; body: string }[];
  pricing: { label: string; value: string; note?: string }[];
  howto: string[];
  faq: { q: string; a: string }[];
  /** affiliate_links.json のキー名。記事のテーマに合う広告だけを指定する */
  affiliates: string[];
};

const GUIDES_DIR = path.join(process.cwd(), "content", "guides");

let cache: Guide[] | null = null;

export function getAllGuides(): Guide[] {
  if (cache) return cache;
  if (!fs.existsSync(GUIDES_DIR)) return (cache = []);
  cache = fs
    .readdirSync(GUIDES_DIR)
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(fs.readFileSync(path.join(GUIDES_DIR, file), "utf8")) as Guide)
    .sort((a, b) => guideArticles(b).length - guideArticles(a).length);
  return cache;
}

/**
 * 製品ガイドへのリンク先。製品のまとめページ（/products）へ移ったガイドは、next.config.ts が 308 で転送するので、
 * リンクは転送先へ直接張る（転送を経由させない。2026-11-04 の点検で /guides/gpt-6-astra への内部リンクが 9 本の記事にあった。台帳 W3）。
 * 規則は next.config.ts の guideRedirects・/guides の一覧と同じ（lib/products.ts の supersedesGuide）
 */
export function guideHref(slug: string): string {
  // Cloudflare では content/ を仮想の fs で読むので、ファイルの有無ではなく、一覧（/guides）・サイトマップと同じ supersedesGuide で決める
  return supersedesGuide(slug) ? `/products/${slug}` : `/guides/${slug}`;
}

export function getGuide(slug: string): Guide | undefined {
  return getAllGuides().find((guide) => guide.slug === slug);
}

const articleCache = new Map<string, ArticleSummary[]>();

/** ガイドに関係するAI記事（新しい順） */
export function guideArticles(guide: Guide): ArticleSummary[] {
  const cached = articleCache.get(guide.slug);
  if (cached) return cached;
  const pattern = new RegExp(guide.match);
  const articles = getAllArticles().filter(
    (article) =>
      article.genreSlug === "ai" && pattern.test(`${article.title} ${article.seoTitle ?? ""}`),
  );
  articleCache.set(guide.slug, articles);
  return articles;
}

/** 記事に対応するガイド（記事ページから案内するため） */
/**
 * 関連記事が1本も無い製品ガイド（元にした記事を取り下げた・話題が消えた）。本文の根拠が無く中身も更新されないので、
 * 一覧・サイトマップ・llms.txt に出さず、検索にも出さない（2026-11-04 の点検で「Jev」のガイドが関連0本のまま検索に出ていた。台帳 W28）
 */
export function isThinGuide(guide: Guide): boolean {
  return guideArticles(guide).length === 0;
}

export function guideForArticle(article: ArticleSummary): Guide | undefined {
  return getAllGuides().find((guide) =>
    new RegExp(guide.match).test(`${article.title} ${article.seoTitle ?? ""}`),
  );
}
