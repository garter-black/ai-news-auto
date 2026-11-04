import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { laterPages, parsePage } from "@/lib/paginate";
import { getAllTags, getArticlesByTag } from "@/lib/articles";
import TagView, { tagMetadata } from "../../view";

type Props = { params: Promise<{ tag: string; n: string }> };

// 2ページ目以降もビルド時に静的化する（デプロイ直後の初回アクセスで作らない。台帳 W29）
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return getAllTags()
    .filter((tag) => tag.count > 60)
    .flatMap((tag) => laterPages(getArticlesByTag(tag.slug).length).map((n) => ({ tag: tag.slug, n })));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { tag, n } = await params;
  const page = parsePage(n);
  return page ? tagMetadata(tag, page) : {};
}

export default async function TagPagedPage({ params }: Props) {
  const { tag, n } = await params;
  const page = parsePage(n);
  if (!page) notFound();
  return <TagView tag={tag} page={page} />;
}
