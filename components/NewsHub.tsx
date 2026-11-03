import Link from "next/link";
import RelTime from "@/components/RelTime";
import Thumb from "@/components/Thumb";
import { smallThumb } from "@/lib/thumb-url";
import type { ArticleSummary } from "@/lib/articles";
import { thumbSource } from "@/lib/companies";
import { displayHeadline } from "@/lib/headline";
import { coverPath } from "@/lib/cover-path";

type GenreChip = { slug: string; label: string; count: number };

/**
 * 話題の絞り込み。今いる話題（"all" は「すべて」）は塗りつぶし、ほかは /genres/<slug> へのリンク。
 * スマホでは横にスクロールさせて1行に収める。
 */
export function GenreChips({ genres, active }: { genres: GenreChip[]; active?: string }) {
  const base = "shrink-0 rounded-md border px-3.5 py-1.5 text-meta font-bold transition-colors";
  const on = `${base} border-ink bg-ink text-paper`;
  const off = `${base} border-line text-ink-soft hover:border-ink hover:text-ink`;
  return (
    <nav
      aria-label="話題で絞り込む"
      className="-mx-5 overflow-x-auto px-5 py-1 [scrollbar-width:none] sm:mx-0 sm:overflow-visible sm:px-0 [&::-webkit-scrollbar]:hidden"
    >
      <ul className="flex gap-2 sm:flex-wrap">
        <li className="shrink-0">
          <Link href="/news" aria-current={active === "all" ? "page" : undefined} className={active === "all" ? on : off}>
            すべて
          </Link>
        </li>
        {genres.map((genre) => (
          <li key={genre.slug} className="shrink-0">
            <Link
              href={`/genres/${genre.slug}`}
              aria-current={active === genre.slug ? "page" : undefined}
              className={active === genre.slug ? on : off}
            >
              {genre.label}
              <span className="index-num ml-1.5 text-meta font-normal opacity-70">{genre.count}</span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/**
 * ニュースの一覧。スマホでは「小さい写真＋見出し」の行、
 * 幅のある画面では写真を上に置いたカードを格子状に並べる。
 */
export function NewsGrid({ articles, priority = 0 }: { articles: ArticleSummary[]; priority?: number }) {
  return (
    <ul className="grid gap-x-6 gap-y-5 sm:grid-cols-2 sm:gap-y-8 lg:grid-cols-4">
      {articles.map((article, index) => (
        <li key={article.slug} className="border-b border-line pb-5 last:border-b-0 sm:border-b-0 sm:pb-0">
          <NewsItem article={article} priority={index < priority} />
        </li>
      ))}
    </ul>
  );
}

function NewsItem({ article, priority }: { article: ArticleSummary; priority: boolean }) {
  const postedAt = article.generatedAt || article.publishedAt;
  return (
    <Link
      href={`/articles/${article.slug}`}
      className="group grid grid-cols-[112px_minmax(0,1fr)] items-start gap-3.5 sm:grid-cols-1 sm:gap-3"
    >
      <span className="relative aspect-video overflow-hidden rounded-lg bg-surface-2 sm:rounded-xl">
        <Thumb
          src={smallThumb(article.thumbnail)}
          source={thumbSource(article)}
          cover={coverPath(article)}
          alt=""
          fill
          priority={priority}
          sizes="(max-width: 640px) 112px, (max-width: 1024px) 50vw, 25vw"
          className="object-cover transition-transform duration-500 group-hover:scale-[1.04]"
        />
      </span>
      <span className="grid min-w-0 gap-1.5">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta">
          <span className="font-bold text-accent">{article.genre}</span>
          <RelTime iso={postedAt} className="index-num font-semibold text-muted" />
        </span>
        <span className="line-clamp-3 text-[14.5px] font-bold leading-[1.6] text-ink decoration-1 underline-offset-4 group-hover:underline sm:text-[15px]">
          {displayHeadline(article.title)}
        </span>
      </span>
    </Link>
  );
}

/** トップの下段に置く「ニュース」の入口。記事が無ければ何も出さない */
export default function NewsHub({ articles, genres }: { articles: ArticleSummary[]; genres: GenreChip[] }) {
  if (articles.length === 0) return null;
  return (
    <section aria-labelledby="news-heading" className="mt-20 border-t-2 border-ink pt-6">
      <div className="mb-5 flex items-end justify-between gap-4">
        <div>
          <p className="eyebrow">NEWS</p>
          <h2 id="news-heading" className="t-h2 mt-1">
            ニュース
          </h2>
          <p className="mt-1 text-meta text-muted">AI以外の話題も、公式発表から要点だけ</p>
        </div>
        <Link href="/news" className="shrink-0 pb-1 text-meta font-bold text-muted hover:text-ink">
          すべて見る →
        </Link>
      </div>
      <div className="mb-7">
        <GenreChips genres={genres} />
      </div>
      <NewsGrid articles={articles} />
    </section>
  );
}
