import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import ArticleCard from "@/components/ArticleCard";
import { GenreChips } from "@/components/NewsHub";
import Pagination from "@/components/Pagination";
import {
  getArticlesByGenre,
  getChannelsByGenre,
} from "@/lib/articles";
import { listingRobots } from "@/lib/indexing";
// ジャンル名はヘッダーのタブと同じ関数（lib/nav.ts）で出す
import { genreLabel } from "@/lib/nav";
import { pageHref, pageSuffix, paginate } from "@/lib/paginate";
import { broadGenresWithArticles, broadSection } from "@/lib/sections";

const GENRE_COPY: Record<string, string> = {
  ai: "OpenAI・Google・Anthropic など世界のAI企業の公式ブログ・発表動画を、公開から最短数分で日本語にまとめます。新機能・料金・日本で使えるか・仕事にどう活かせるかまで整理しています。",
  // 投資・マネーと不動産の説明は lib/sections.ts の BROAD_SECTIONS（/news・トップと同じ文）
};


/** ジャンルの説明文 */
function genreCopy(genre: string): string | undefined {
  return GENRE_COPY[genre] ?? broadSection(genre)?.description;
}

/** ジャンル一覧の metadata（1ページ目と2ページ目以降で共通） */
export function genreMetadata(genre: string, page: number): Metadata {
  const label = genreLabel(genre);
  const items = getArticlesByGenre(genre);
  const count = items.length;
  const broad = broadSection(genre);

  return {
    title: `${label}の最新ニュース・解説記事${pageSuffix(page)}`,
    description: broad
      ? `${label}の最新ニュースを${count}本掲載。${broad.description}`
      : `${label}の最新ニュースを${count}本掲載。企業・官公庁などの公式発表をもとに、要点・数字・注意点まで日本語でまとめています。`,
    alternates: { canonical: pageHref(`/genres/${genre}`, page) },
    // 検索対象の記事が少ないジャンル（AI以外の話題など）は noindex
    ...listingRobots(items),
  };
}

export default function GenreView({ genre, page }: { genre: string; page: number }) {
  const articles = getArticlesByGenre(genre);
  if (articles.length === 0) notFound();
  const paged = paginate(articles, page);
  if (page > paged.totalPages) notFound();

  const label = genreLabel(genre);
  const broad = broadSection(genre);
  // 幅広い話題は発信元が多くばらつくので、発信元ではなく話題の切り替えを出す
  const channels = broad ? [] : getChannelsByGenre(genre);

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-8">
      <p className="text-meta font-bold tracking-[0.2em] text-accent">{broad ? "NEWS" : "GENRE"}</p>
      <h1 className="t-h1 mt-3">
        {label}の最新ニュース・解説{pageSuffix(page)}
      </h1>
      <p className="t-lead mt-4">
        {genreCopy(genre) ?? `${label}に関する解説記事を${articles.length}本掲載しています。`}
      </p>

      {broad && (
        <div className="mt-6">
          <GenreChips genres={broadGenresWithArticles()} active={genre} />
        </div>
      )}

      {channels.length > 0 && (
        <div className="mt-6 flex flex-wrap gap-2">
          {channels.map((channel) => (
            <Link
              key={channel.slug}
              href={`/channels/${channel.slug}`}
              className="rounded-md border border-line bg-surface px-3.5 py-1.5 text-meta text-ink-soft transition-colors hover:border-accent hover:text-accent"
            >
              {channel.label}
              <span className="ml-1.5 text-muted">{channel.count}</span>
            </Link>
          ))}
        </div>
      )}

      <div className="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {paged.items.map((article, index) => (
          <ArticleCard
            key={article.slug}
            article={article}
            priority={page === 1 && index < 3}
          />
        ))}
      </div>

      <Pagination base={`/genres/${genre}`} page={page} totalPages={paged.totalPages} />
    </div>
  );
}
