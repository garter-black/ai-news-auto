import type { Metadata } from "next";
import SearchView from "./search-view";
import { getAllArticles } from "@/lib/articles";
import { genreLabel, GENRE_NAV_ORDER } from "@/lib/nav";
import { searchChunks, searchVersion } from "@/lib/search-index.mjs";
import { getAllGenres } from "@/lib/articles";

export const metadata: Metadata = {
  title: "記事を検索",
  description: "サイソクニュースの記事を、言葉で探せます。会社名・サービス名・制度の名前（NISA・補助金など）で検索できます。",
  alternates: { canonical: "/search" },
  // 検索結果のページは検索エンジンに載せない（リンクはたどってよい）
  robots: { index: false, follow: true },
};

/** よく探される言葉（入力の前に出す。押すとその言葉で検索） */
const SUGGESTIONS = ["ChatGPT", "Claude", "Gemini", "NISA", "金利", "補助金", "iPhone", "住宅ローン"];

/**
 * サイト内検索。ページは静的に作り、検索はブラウザで行う（サーバーの処理を使わない）。
 * 索引は public/search-index/<n>.json（scripts/build_index.mjs が lib/search-index.mjs で作る）。
 */
export default function SearchPage() {
  const articles = getAllArticles();
  const chunks = searchChunks(articles).length;
  const version = searchVersion(articles);
  const genres = Object.fromEntries(getAllGenres().map((g) => [g.slug, genreLabel(g.slug)]));
  const order = [...GENRE_NAV_ORDER] as string[];

  return (
    <div className="mx-auto max-w-[920px] px-4 py-8 sm:px-8 sm:py-12">
      <h1 className="t-h1">記事を検索</h1>
      <p className="mt-2 text-note text-muted">新しい記事から最大1,000本を、題名・要約・タグで探します。</p>
      <SearchView chunks={chunks} version={version} genres={genres} genreOrder={order} suggestions={SUGGESTIONS} />
    </div>
  );
}
