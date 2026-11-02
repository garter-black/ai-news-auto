import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ArticleCard from "@/components/ArticleCard";
import Pagination from "@/components/Pagination";
import { getArticlesByTag, getTagLabel, tagSlug } from "@/lib/articles";
import { listingRobots } from "@/lib/indexing";
import { pageHref, pageSuffix, paginate } from "@/lib/paginate";

/** タグ一覧の metadata（1ページ目と2ページ目以降で共通） */
export function tagMetadata(tag: string, page: number): Metadata {
  const label = getTagLabel(tag);
  const articles = getArticlesByTag(tag);
  const genres = [...new Set(articles.map((a) => a.genre))].join("・");
  const base = `/tags/${encodeURIComponent(tagSlug(label))}`;

  return {
    title: `${label} の最新ニュース・解説記事${pageSuffix(page)}`,
    description: `${label} に関する${genres}の解説記事を ${articles.length} 本掲載。公式発表や専門家の動画をAIが解析し、要点・数字・注意点まで日本語でまとめています。`,
    alternates: { canonical: pageHref(base, page) },
    // 検索対象の記事が少ない薄いタグは検索対象から外す
    ...listingRobots(articles),
  };
}

export default function TagView({ tag, page }: { tag: string; page: number }) {
  const articles = getArticlesByTag(tag);
  if (articles.length === 0) notFound();
  const paged = paginate(articles, page);
  if (page > paged.totalPages) notFound();

  const label = getTagLabel(tag);
  const genres = [...new Set(articles.map((a) => a.genre))].join("・");

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-8">
      <p className="eyebrow">TOPIC</p>
      <h1 className="t-h1 mt-2">
        {label} の最新ニュース・解説{pageSuffix(page)}
      </h1>
      <p className="t-lead mt-4">
        {label} に関連する{genres}
        の動画をAIが映像・音声ごと解析し、要点・数字・注意点まで日本語でまとめた記事が
        {articles.length} 本あります。
      </p>

      <div className="mt-10 grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
        {paged.items.map((article, index) => (
          <ArticleCard
            key={article.slug}
            article={article}
            priority={page === 1 && index < 3}
          />
        ))}
      </div>

      <Pagination base={`/tags/${encodeURIComponent(tagSlug(label))}`} page={page} totalPages={paged.totalPages} />
    </div>
  );
}
