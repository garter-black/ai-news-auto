import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { laterPages, parsePage } from "@/lib/paginate";
import { getAllChannels, getArticlesByChannel } from "@/lib/articles";
import ChannelView, { channelMetadata } from "../../view";

type Props = { params: Promise<{ channel: string; n: string }> };

// 2ページ目以降もビルド時に静的化する（デプロイ直後の初回アクセスで作らない。台帳 W29）
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return getAllChannels().flatMap((c) => laterPages(getArticlesByChannel(c.slug).length).map((n) => ({ channel: c.slug, n })));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { channel, n } = await params;
  const page = parsePage(n);
  return page ? channelMetadata(channel, page) : {};
}

export default async function ChannelPagedPage({ params }: Props) {
  const { channel, n } = await params;
  const page = parsePage(n);
  if (!page) notFound();
  return <ChannelView channel={channel} page={page} />;
}
