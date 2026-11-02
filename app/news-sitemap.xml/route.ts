import { newsSitemapReason } from "@/lib/indexing";
import { getAllArticles, lastModifiedOf } from "@/lib/articles";
import { siteConfig } from "@/site.config";

export const dynamic = "force-static";

const escapeXml = (value: string) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");

/**
 * Google ニュース用サイトマップ。直近2日以内に公開した記事だけを載せる（Google の仕様）。
 * ビルドは記事が増えるたびに走るので、常に新しい記事が並ぶ。
 * 載せる基準は lib/indexing.ts の newsSitemapReason（重複・角度違いの解説・まとめ・元の発表から48時間以上たった記事は載せない）。
 * 公開日は最初に載せた時刻（generatedAt）のまま。続報は lastmod にだけ反映する。
 * news:name は Google ニュースの媒体名（サイト名＝WebSite 構造化データの name）と一致させる。
 */
export function GET() {
  const now = Date.now();
  const recent = getAllArticles()
    .filter((a) => newsSitemapReason(a, now) === null)
    .slice(0, 1000);

  const items = recent
    .map(
      (a) => `  <url>
    <loc>${siteConfig.url}/articles/${a.slug}</loc>
    <lastmod>${new Date(lastModifiedOf(a)).toISOString()}</lastmod>
    <news:news>
      <news:publication>
        <news:name>${escapeXml(siteConfig.siteName)}</news:name>
        <news:language>ja</news:language>
      </news:publication>
      <news:publication_date>${new Date(a.generatedAt || a.publishedAt).toISOString()}</news:publication_date>
      <news:title>${escapeXml(a.seoTitle || a.title)}</news:title>
    </news:news>
  </url>`,
    )
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
${items}
</urlset>
`;
  return new Response(xml, { headers: { "Content-Type": "application/xml; charset=utf-8" } });
}
