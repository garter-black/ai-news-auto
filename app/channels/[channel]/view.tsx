import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ArticleCard from "@/components/ArticleCard";
import Pagination from "@/components/Pagination";
import { getAllChannels, getArticlesByChannel, isWeb } from "@/lib/articles";
import { isListingIndexable } from "@/lib/indexing";
import {
  channelDescription as describe,
  channelDisplayLabel,
  channelTitleTail as titleTail,
  isNewsOutletChannel,
  isThinSourceChannel,
  RADAR_OFFICIAL,
} from "@/lib/news-outlets";
import { pageHref, pageSuffix, paginate } from "@/lib/paginate";

/** チャンネル一覧の metadata（1ページ目と2ページ目以降で共通） */
export function channelMetadata(channel: string, page: number): Metadata {
  const label = channelLabel(channel);
  const items = getArticlesByChannel(channel);
  const count = items.length;
  // AI記事が無い・検索対象の記事が少ないチャンネルは検索対象から外す。
  // 報道機関の発信元（radar-<報道機関>）と、各社が混ざった radar-official は薄いページなので検索に出さない（台帳 W12）
  const indexable = !isThinSourceChannel(channel) && items.some((a) => a.genreSlug === "ai") && isListingIndexable(items);

  return {
    title: `${label}${titleTail(channel)}${pageSuffix(page)}`,
    description: describe(channel, label, items[0] && isWeb(items[0]), count),
    alternates: { canonical: pageHref(`/channels/${channel}`, page) },
    ...(indexable ? {} : { robots: { index: false, follow: true } }),
  };
}

export default function ChannelView({ channel, page }: { channel: string; page: number }) {
  const articles = getArticlesByChannel(channel);
  if (articles.length === 0) notFound();
  const paged = paginate(articles, page);
  if (page > paged.totalPages) notFound();

  const label = channelLabel(channel);
  const latest = articles[0];
  const web = isWeb(latest);
  const outlet = isNewsOutletChannel(channel);
  // web の発信元はサイトのトップ、動画はチャンネルへリンクする。各社が混ざる radar-official と話題のまとめはリンクしない
  const home =
    channel === RADAR_OFFICIAL || channel === "trends"
      ? ""
      : web
        ? siteOrigin(latest.sourceUrl)
        : `https://www.youtube.com/channel/${latest.channelId}`;
  const homeLabel = outlet ? `${label}のサイトを見る` : web ? "公式サイトを見る" : "公式チャンネルを見る";

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-12 sm:px-8">
      <p className="text-meta font-bold tracking-widest text-accent">発信元</p>
      <h1 className="t-h1 mt-2">
        {label}
        {titleTail(channel)}
        {pageSuffix(page)}
      </h1>
      <p className="t-lead mt-3">
        {describe(channel, label, web, articles.length)}
        {home && (
          <a href={home} target="_blank" rel="noopener noreferrer nofollow" className="ml-1 text-accent underline">
            {homeLabel}
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

/** 発信元ページの名前（radar-official・話題のまとめは実態どおりの名前） */
function channelLabel(channel: string): string {
  return channelDisplayLabel(channel, getAllChannels().find((c) => c.slug === channel)?.label ?? channel);
}

/** URL のサイトトップ（不正な URL なら空） */
function siteOrigin(url?: string): string {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
}
