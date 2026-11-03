import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import ConceptArticle from "@/components/article-concept/ConceptArticle";
import { buildConceptView } from "@/components/article-concept/view";
import { getArticle } from "@/lib/articles";

/**
 * 記事ページの新コンセプト「引き算のAI」の確認用ページ。?slug= で任意の記事を描ける（既定は GPT-6.1 Terra）。
 * 検索には出さない（noindex）・ナビやサイトマップからはリンクしない。
 * 本番では、自社の確認（ヘッダー X-Saisoku-Internal: 1 か ?_internal=1）のときだけ開き、ほかは 404（2026-11-03 の点検 #12。
 * 審査の人や読者が試作の「何がすごいの？」の型を見ないように）。手元（next dev）ではいつでも開く。
 * 既定の記事は cloudflare/worker.ts の先読みと揃える。
 */

const DEFAULT_SLUG = "openai-gpt-6-1-terra-announcement";

/** 確認用：性格の違う記事（続報あり・AI以外・判定なし） */
const SAMPLES: { slug: string; label: string }[] = [
  { slug: DEFAULT_SLUG, label: "GPT-6.1 Terra（写真なし）" },
  { slug: "iphone-duo-arrives-at-apple-store", label: "iPhone Duo（ガジェット・続報あり）" },
];

type Props = { searchParams: Promise<{ slug?: string; _internal?: string }> };

/** 本番で開いてよい自社の確認か */
function conceptAllowed(env: string | undefined, header: string | null, internalParam: string | undefined): boolean {
  return env !== "production" || header === "1" || internalParam === "1";
}

export const metadata: Metadata = {
  title: "記事ページ新コンセプト（確認用）",
  robots: { index: false, follow: false, nocache: true },
};

export default async function ArticleConceptPage({ searchParams }: Props) {
  const { slug = DEFAULT_SLUG, _internal } = await searchParams;
  if (!conceptAllowed(process.env.NODE_ENV, (await headers()).get("x-saisoku-internal"), _internal)) notFound();
  const article = getArticle(slug);
  if (!article) notFound();
  const view = await buildConceptView(article);

  return (
    <>
      <ConceptArticle view={view} affiliates={article.affiliates ?? []} />
      <nav className="cx-proto" aria-label="確認用">
        確認用ページ（検索には出ません）。ほかの記事で見る：
        {SAMPLES.filter((s) => s.slug !== slug).map((s, i) => (
          <span key={s.slug}>
            {i > 0 && "／"}
            <Link href={s.slug === DEFAULT_SLUG ? "/article-concept" : `/article-concept?slug=${s.slug}`}>{s.label}</Link>
          </span>
        ))}
      </nav>
    </>
  );
}
