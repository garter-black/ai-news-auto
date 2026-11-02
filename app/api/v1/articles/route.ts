import { apiJson, authorize, handleOptions } from "@/lib/api-auth";
import { getAllArticles, getArticle, type FullArticle } from "@/lib/articles";
import { siteConfig } from "@/site.config";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const MAX_LIMIT = 100;

/** 一覧では本文を返さない。必要なら ?include=body で明示的に取得させる */
function serialize(article: FullArticle, includeBody: boolean) {
  const base = {
    slug: article.slug,
    url: `${siteConfig.url}/articles/${article.slug}`,
    title: article.title,
    lede: article.lede,
    description: article.description,
    genre: article.genre,
    genreSlug: article.genreSlug,
    channel: article.channel,
    channelSlug: article.channelSlug,
    tags: article.tags,
    keywords: article.keywords,
    sourceType: article.sourceType ?? "video",
    ...(article.sourceType === "web" ? { sourceUrl: article.sourceUrl, sourceName: article.sourceName } : {}),
    videoId: article.videoId,
    videoUrl: article.videoUrl,
    thumbnail: article.thumbnail,
    publishedAt: article.publishedAt,
    generatedAt: article.generatedAt,
    charCount: article.body.length,
    affiliateCount: article.affiliates.length,
  };
  return includeBody ? { ...base, body: article.body } : base;
}

export function OPTIONS(request: Request) {
  return handleOptions(request);
}

export function GET(request: Request) {
  const auth = authorize(request);
  if (!auth.ok) return auth.response;

  const params = new URL(request.url).searchParams;
  const limit = Math.min(
    Math.max(Number.parseInt(params.get("limit") ?? "20", 10) || 20, 1),
    MAX_LIMIT,
  );
  const offset = Math.max(Number.parseInt(params.get("offset") ?? "0", 10) || 0, 0);
  const genre = params.get("genre");
  const channel = params.get("channel");
  const since = params.get("since"); // ISO8601。生成日時で絞る
  const includeBody = params.get("include") === "body";

  let items = getAllArticles();
  if (genre) items = items.filter((a) => a.genreSlug === genre);
  if (channel) items = items.filter((a) => a.channelSlug === channel);
  if (since) {
    const threshold = new Date(since).getTime();
    if (!Number.isNaN(threshold)) {
      items = items.filter((a) => new Date(a.generatedAt).getTime() >= threshold);
    }
  }

  const page = items.slice(offset, offset + limit);

  return apiJson(request, {
    total: items.length,
    limit,
    offset,
    hasMore: offset + page.length < items.length,
    // 一覧の索引には本文が無いので、返す分（最大100本）だけ記事ファイルを読む
    items: page.flatMap((a) => {
      const full = getArticle(a.slug);
      return full ? [serialize(full, includeBody)] : [];
    }),
  });
}
