import { isDistributionEligible } from "@/lib/indexing";
import { coverPath, getAllArticles, getArticle, sourceHref, sourceLabel, type ArticleSummary, type FullArticle } from "@/lib/articles";
import { renderMarkdown } from "@/lib/markdown";
import { isAffiliateUrl } from "@/lib/affiliate";
import { siteConfig } from "@/site.config";

/**
 * ニュースアプリ（スマートニュース・グノシーなど）向けフィードの共通部品。
 * フィードはビルド時に静的化する（force-static）ので、Workers の実行時には本文を読まない。
 * 配信対象は検索対象と分け、従来の配信基準を維持する。
 */

export const escapeXml = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");

/** CDATA で包む（中に "]]>" があっても壊れないように分割） */
export const cdata = (value: string) => `<![CDATA[${value.replace(/]]>/g, "]]]]><![CDATA[>")}]]>`;

/** RFC822 の日時（例: Tue, 27 Oct 2026 09:30:00 +0000） */
export const rfc822 = (iso: string) => new Date(iso).toUTCString().replace(/GMT$/, "+0000");

/** サイソクに載せた日時（ニュースサイトマップ・構造化データと同じ基準） */
export const publishedOf = (a: ArticleSummary) => a.generatedAt || a.publishedAt;

export const articleUrl = (a: ArticleSummary) => `${siteConfig.url}/articles/${a.slug}`;
export const coverUrl = (a: ArticleSummary) => `${siteConfig.url}${coverPath(a)}`;

/** <a> を外して中の文字だけ残す */
const unwrap = (html: string, keep: (href: string) => boolean) =>
  html.replace(/<a\s[^>]*?href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi, (whole, href: string, text: string) => (keep(href) ? whole : text));

/** 表を「見出し：値」の段落に開く（表に対応しないアプリ向け） */
function flattenTables(html: string): string {
  return html.replace(/<table[\s\S]*?<\/table>/gi, (table) => {
    const rows = [...table.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].map((row) =>
      [...row[1].matchAll(/<t[hd][^>]*>([\s\S]*?)<\/t[hd]>/gi)].map((cell) => cell[1].trim()),
    );
    if (rows.length === 0) return "";
    const [head, ...body] = rows;
    if (body.length === 0) return `<p>${head.join("／")}</p>`;
    return body
      .map((cells) => `<p>${cells.map((cell, i) => (head[i] && i > 0 ? `${head[i]}：${cell}` : `<strong>${cell}</strong>`)).join("<br />")}</p>`)
      .join("\n");
  });
}

export type FeedHtmlOptions = {
  /** 本文中のリンクを残すか（グノシーは出典以外のリンクを原則置けない） */
  links: boolean;
  /** 表を残すか（グノシーは table 非対応） */
  tables: boolean;
};

/** 記事の全文 HTML（リード・要点・本文・出典・AI利用の開示）。JS・CSS・広告は含めない */
export async function feedHtml(article: FullArticle, options: FeedHtmlOptions): Promise<string> {
  const affiliateUrls = new Set((article.affiliates ?? []).map((a) => a.url));
  let body = await renderMarkdown(article.body);
  body = body
    // サイト内リンクは絶対URLに
    .replace(/(href|src)="\/(?!\/)/g, `$1="${siteConfig.url}/`)
    // 配信先では不要な属性
    .replace(/\s(?:id|class|style|target|align)="[^"]*"/g, "")
    .replace(/\srel="[^"]*"/g, "");
  body = unwrap(body, (href) => options.links && /^https?:\/\//.test(href) && !isAffiliateUrl(href.replace(/&amp;/g, "&"), affiliateUrls) && !affiliateUrls.has(href));
  if (!options.tables) body = flattenTables(body);

  const parts: string[] = [];
  if (article.lede) parts.push(`<p>${escapeXml(article.lede)}</p>`);
  if (article.highlights.length > 0) {
    parts.push(`<h2>3秒でわかる要点</h2>`, `<ol>${article.highlights.map((h) => `<li>${escapeXml(h)}</li>`).join("")}</ol>`);
  }
  parts.push(body.trim());

  const src = sourceHref(article);
  if (src) parts.push(`<p>出典：<a href="${escapeXml(src)}">${escapeXml(sourceLabel(article))}</a></p>`);
  const policy = `${siteConfig.url}/editorial-policy`;
  parts.push(
    options.links
      ? `<p>この記事は生成AIを使って作成し、元の発表と照合してから公開しています。制作方法と事実確認の手順は<a href="${policy}">編集方針</a>をご覧ください。</p>`
      : `<p>この記事は生成AIを使って作成し、元の発表と照合してから公開しています。制作方法と事実確認の手順は${siteConfig.siteName}の編集方針（${policy}）をご覧ください。</p>`,
  );
  return parts.join("\n");
}

/**
 * フィードに載せる記事（新しい順・本文つき）。予約投稿のような未来の日時は除く。
 * maxAgeDays より古い記事は配信先が取り込まないので載せない
 */
export function pickFeedArticles({ limit, maxAgeDays }: { limit: number; maxAgeDays: number }): FullArticle[] {
  const now = Date.now();
  return getAllArticles()
    .filter(isDistributionEligible)
    .filter((a) => {
      const age = now - Date.parse(publishedOf(a));
      return age >= 0 && age < maxAgeDays * 86_400_000;
    })
    .slice(0, limit)
    .map((a) => getArticle(a.slug))
    .filter((a): a is FullArticle => Boolean(a));
}

/** item の XML を作り、合計サイズが上限を超えたところで打ち切る（最低5件は残す） */
const encoder = new TextEncoder();

export async function buildItems(
  articles: FullArticle[],
  maxBytes: number,
  render: (article: FullArticle) => Promise<string>,
): Promise<string[]> {
  const items: string[] = [];
  let total = 0;
  for (const article of articles) {
    const item = await render(article);
    total += encoder.encode(item).length;
    if (total > maxBytes && items.length >= 5) break;
    items.push(item);
  }
  return items;
}

/** フィードのレスポンス。中身はデプロイごとに固定（エッジに置かれ、デプロイで入れ替わる） */
export function feedResponse(xml: string): Response {
  return new Response(xml, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, max-age=600, s-maxage=600",
    },
  });
}
