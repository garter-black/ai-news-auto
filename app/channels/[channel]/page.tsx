import type { Metadata } from "next";
import { getAllChannels } from "@/lib/articles";
import ChannelView, { channelMetadata } from "./view";

type Props = { params: Promise<{ channel: string }> };

// チャンネル（発信元）はすべてビルド時に静的化する（台帳 W29。100件弱で軽い）。無いチャンネルは 404
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return getAllChannels().map((channel) => ({ channel: channel.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { channel } = await params;
  return channelMetadata(channel, 1);
}

export default async function ChannelPage({ params }: Props) {
  const { channel } = await params;
  return <ChannelView channel={channel} page={1} />;
}
