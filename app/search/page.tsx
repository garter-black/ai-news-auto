import type { Metadata } from "next";
import SearchView from "./search-view";
import { getAllArticles } from "@/lib/articles";
import { genreLabel, GENRE_NAV_ORDER } from "@/lib/nav";
import { searchChunks, searchExclusion, searchVersion } from "@/lib/search-index.mjs";
import { noindexReason } from "@/lib/indexing";
import { retiredSlugs } from "@/lib/retired";
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
  // 取り下げた記事・検索に出さない記事は索引に入れない（scripts/build_index.mjs と同じ判定。台帳 W24）
  const exclude = searchExclusion(noindexReason, retiredSlugs());
  const chunks = searchChunks(articles, exclude).length;
  const version = searchVersion(articles, exclude);
  const genres = Object.fromEntries(getAllGenres().map((g) => [g.slug, genreLabel(g.slug)]));
  const order = [...GENRE_NAV_ORDER] as string[];

  return (
    <div className="mx-auto max-w-[920px] px-4 py-8 sm:px-8 sm:py-12">
      <h1 className="t-h1">記事を検索</h1>
      <p className="mt-2 text-note text-muted">新しい記事から最大1,000本を、題名・要約・タグで探します。題名に入っている記事を先に、同じなら新しい順に並べます。</p>
      <SearchView chunks={chunks} version={version} genres={genres} genreOrder={order} suggestions={SUGGESTIONS} />
    </div>
  );
}
