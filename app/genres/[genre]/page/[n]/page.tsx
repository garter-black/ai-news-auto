import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { laterPages, parsePage } from "@/lib/paginate";
import { getAllGenres, getArticlesByGenre } from "@/lib/articles";
import GenreView, { genreMetadata } from "../../view";

type Props = { params: Promise<{ genre: string; n: string }> };

// 2ページ目以降もビルド時に静的化する（デプロイ直後の初回アクセスで作らない。台帳 W29）
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return getAllGenres().flatMap((g) => laterPages(getArticlesByGenre(g.slug).length).map((n) => ({ genre: g.slug, n })));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { genre, n } = await params;
  const page = parsePage(n);
  return page ? genreMetadata(genre, page) : {};
}

export default async function GenrePagedPage({ params }: Props) {
  const { genre, n } = await params;
  const page = parsePage(n);
  if (!page) notFound();
  return <GenreView genre={genre} page={page} />;
}
