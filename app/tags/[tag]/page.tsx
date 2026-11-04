import type { Metadata } from "next";
import { getAllTags } from "@/lib/articles";
import TagView, { tagMetadata } from "./view";

type Props = { params: Promise<{ tag: string }> };

// 2本以上の記事があるタグ（サイトマップに載せるタグはすべてこの中）はビルド時に静的化する（台帳 W29）。
// 1本だけのタグは検索に出さないページなので、初回アクセス時に生成（ISR）。無いタグは 404
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  // ここに渡すのは「デコード済み」の値。エンコードすると二重エンコードで404になる。
  return getAllTags()
    .filter((tag, i) => i < 100 || tag.count >= 2)
    .map((tag) => ({ tag: tag.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { tag } = await params;
  return tagMetadata(tag, 1);
}

export default async function TagPage({ params }: Props) {
  const { tag } = await params;
  return <TagView tag={tag} page={1} />;
}
