import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ArticleCard from "@/components/ArticleCard";
import Pagination from "@/components/Pagination";
import { getAllChannels, getArticlesByChannel, isWeb } from "@/lib/articles";
import { isListingIndexable } from "@/lib/indexing";
import { pageHref, pageSuffix, paginate } from "@/lib/paginate";

/** チャンネル一覧の metadata（1ページ目と2ページ目以降で共通） */
export function channelMetadata(channel: string, page: number): Metadata {
  const label = getAllChannels().find((c) => c.slug === channel)?.label ?? channel;
  const items = getArticlesByChannel(channel);
  const count = items.length;
  // AI記事が無い・検索対象の記事が少ないチャンネルは検索対象から外す
  const indexable = items.some((a) => a.genreSlug === "ai") && isListingIndexable(items);
  // 公式ブログ・プレスリリースの発信元は「YouTube動画」と書かない
  const what = items[0] && isWeb(items[0]) ? "公式発表" : "YouTube動画";

  return {
    title: `${label} の最新発表まとめ${pageSuffix(page)}`,
    description: `${label} の${what}をAIが解析した日本語解説記事 ${count} 本。要点・数字・注意点まで最速でチェックできます。`,
    alternates: { canonical: pageHref(`/channels/${channel}`, page) },
    ...(indexable ? {} : { robots: { index: false, follow: true } }),
  };
}

export default function ChannelView({ channel, page }: { channel: string; page: number }) {
  const articles = getArticlesByChannel(channel);
  if (articles.length === 0) notFound();
  const paged = paginate(articles, page);
  if (page > paged.totalPages) notFound();

  const label = getAllChannels().find((c) => c.slug === channel)?.label ?? channel;
  const latest = articles[0];
  const web = isWeb(latest);
  // web の発信元はサイトのトップ、動画はチャンネルへリンクする
  const home = web ? siteOrigin(latest.sourceUrl) : `https://www.youtube.com/channel/${latest.channelId}`;

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-8">
      <p className="text-meta font-bold tracking-widest text-accent">CHANNEL</p>
      <h1 className="t-h1 mt-2">
        {label} の最新発表まとめ{pageSuffix(page)}
      </h1>
      <p className="t-lead mt-3">
        {label}{" "}
        {web
          ? "で公開された公式ブログ・プレスリリースをAIが解析し、日本語の解説記事として"
          : "のYouTubeチャンネルで公開された動画をAIが映像・音声ごと解析し、日本語の解説記事として"}
        {articles.length} 本公開中。
        {home && (
          <a href={home} target="_blank" rel="noopener noreferrer nofollow" className="ml-1 text-accent underline">
            {web ? "公式サイトを見る" : "公式チャンネルを見る"}
          </a>
        )}
      </p>

      <div className="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {paged.items.map((article, index) => (
          <ArticleCard
            key={article.slug}
            article={article}
            priority={page === 1 && index < 3}
          />
        ))}
      </div>

      <Pagination base={`/channels/${channel}`} page={page} totalPages={paged.totalPages} />
    </div>
  );
}

/** URL のサイトトップ（不正な URL なら空） */
function siteOrigin(url?: string): string {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
}
