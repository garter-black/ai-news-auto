import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { laterPages, parsePage } from "@/lib/paginate";
import { getBroadArticles } from "@/lib/sections";
import NewsView, { newsMetadata, PER_PAGE as NEWS_PER_PAGE } from "../../view";

type Props = { params: Promise<{ n: string }> };

// 2ページ目以降もビルド時に静的化する（デプロイ直後の初回アクセスで作らない。台帳 W29）
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return laterPages(getBroadArticles().length, NEWS_PER_PAGE).map((n) => ({ n }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const page = parsePage((await params).n);
  return page ? newsMetadata(page) : {};
}

export default async function NewsPagedPage({ params }: Props) {
  const page = parsePage((await params).n);
  if (!page) notFound();
  return <NewsView page={page} />;
}
