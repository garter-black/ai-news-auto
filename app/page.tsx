import Link from "next/link";
import CompanyMark from "@/components/CompanyMark";
import HomeHero from "@/components/HomeHero";
import { isNewsOutletChannel } from "@/lib/news-outlets";
import HomeAssets from "@/components/HomeAssets";
import CompanyChips from "@/components/CompanyChips";
import NewsHub from "@/components/NewsHub";
import Thumb from "@/components/Thumb";
import { smallThumb } from "@/lib/thumb-url";
import Timeline from "@/components/Timeline";
import RailRanking from "@/components/rail/RailRanking";
import StoryRail from "@/components/story/SwipeDeck";
import { getStoryRailBundles } from "@/lib/stories";
import { offerForArticle } from "@/lib/offers";
import { getAllArticles, getAllGenres, tagSlug, type ArticleSummary } from "@/lib/articles";
import { companyCounts, thumbSource } from "@/lib/companies";
import { broadGenresWithArticles, getBroadArticles, isBroadGenre } from "@/lib/sections";
import type { Metadata } from "next";
import { siteConfig } from "@/site.config";
import { coverPath } from "@/lib/cover-path";
import { alsoCoveredSentence } from "@/lib/coverage";

export const revalidate = false;

// トップだけ og:url を自分（＝サイトのURL）にする。下層ページに引き継がせないため layout には置かない
export const metadata: Metadata = {
  openGraph: {
    type: "website",
    siteName: siteConfig.siteName,
    locale: siteConfig.locale,
    url: siteConfig.url,
    title: `${siteConfig.siteName} AI・ビジネス・お金の新しい動きが最速でわかる`,
    description: siteConfig.description,
  },
};

const TIMELINE_SIZE = 24;
const NEWS_SIZE = 12;

/**
 * トップ＝速報デスク。
 * 「いつ（時系列）」と「どこが（企業）」の2軸で組み、
 * 一番上には常にいちばん新しい発表を置く（古いまとめ記事を主役にしない）。
 */
export default function HomePage() {
  // AI・テクノロジーに一点集中しているので、トップはAIジャンルだけで組む
  const ai = getAllArticles().filter((a) => a.genreSlug === "ai");
  if (ai.length === 0) return <EmptyState />;

  // 一番上の大きな記事は公式の発表から（報道をもとにした小さな話を主役にしない。2026-11-03 の点検・台帳 W17）。
  // 直近12時間の新しい順で最初の公式の記事。無ければいちばん新しい記事
  const newest = Date.parse(ai[0].publishedAt);
  const lead =
    ai.slice(0, 8).find((a) => !isNewsOutletChannel(a.channelSlug) && newest - Date.parse(a.publishedAt) <= 12 * 3_600_000) ?? ai[0];
  const timeline = ai.filter((a) => a.slug !== lead.slug).slice(0, TIMELINE_SIZE);
  const roundups = ai.filter((a) => a.type === "roundup" && a.slug !== lead.slug).slice(0, 5);

  // トップにもPR枠。いちばん新しい話題に合う商品を出す（話題が合わなければ汎用の枠）
  const found = offerForArticle(lead, 9);
  const offer = found && { ...found, eyebrow: "今日の話題に合わせて" };

  const companies = companyCounts(ai);
  const tags = topTags(ai, 14);
  // AI以外の幅広い話題は、AIの新着の下に「ニュース」としてまとめる
  const broad = getBroadArticles();
  const broadGenres = broadGenresWithArticles(broad);
  // 以前扱っていた分野（不動産・投資など）。幅広い話題は「ニュース」側に出すので除く
  const otherGenres = getAllGenres().filter((g) => g.slug !== "ai" && !isBroadGenre(g.slug));

  const bundles = getStoryRailBundles();

  return (
    <>
      {bundles.length > 0 && (
        <div className="mx-auto max-w-[1240px]">
          <StoryRail bundles={bundles} />
        </div>
      )}
      <CompanyChips companies={companies.slice(0, 8)} />

      <div className="mx-auto max-w-[1240px] px-5 pb-8 pt-8 sm:px-8 sm:pt-10">
        <HomeHero lead={lead} />

        {offer && (
          <div className="mt-12">
            <RailRanking offer={offer} wide title={offer.headline} />
          </div>
        )}

        <nav aria-label="AIの使い方と費用" data-funnel-pos="home-guides" className="mt-10 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-2 border-y border-line py-4">
          <Link href="/choose/dify-hosting" className="text-[16px] font-bold leading-relaxed text-ink underline-offset-4 hover:text-accent hover:underline">
            DifyにVPSは必要？無料で試す方法と費用を比べる →
          </Link>
          <Link href="/guides" className="text-note font-semibold text-accent underline underline-offset-4">AIの使い方ガイド一覧</Link>
          <div className="mt-2 flex w-full flex-wrap gap-x-6 gap-y-2">
            <Link href="/guides/dify-free-api-cost" className="text-note font-semibold text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-accent">
              Difyは無料？200クレジットとAPI料金を確認
            </Link>
            <Link href="/guides/dify-1-18-startup-check" className="text-note font-semibold text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-accent">
              Dify 1.18.0が起動しないときの確認手順
            </Link>
          </div>
        </nav>

        <HomeAssets />

        <div className="mt-14 grid gap-14 lg:grid-cols-[minmax(0,1fr)_300px] lg:gap-12">
          <section aria-labelledby="latest-heading" className="min-w-0">
            <div className="mb-2 flex items-end justify-between gap-4">
              <h2 id="latest-heading" className="t-h2">
                新着
              </h2>
              <Link href="/articles" className="pb-1 text-meta font-bold text-muted hover:text-ink">
                すべての記事 →
              </Link>
            </div>
            <Timeline articles={timeline} filterable />
            <Link
              href="/articles"
              className="mt-8 flex items-center justify-center rounded-full border border-line-strong py-3 text-note font-bold text-ink transition-colors hover:border-ink"
            >
              もっと見る
            </Link>
          </section>

          <aside className="grid content-start gap-12 lg:sticky lg:top-24">
            {roundups.length > 0 && (
              <section aria-labelledby="roundups-heading" id="roundups">
                <RailHead id="roundups-heading" title="話題まるごと解説" note="同じ話題の発表を1本に" />
                <ol className="grid gap-4">
                  {roundups.map((roundup) => (
                    <RailRoundup key={roundup.slug} article={roundup} />
                  ))}
                </ol>
              </section>
            )}

            <section aria-labelledby="companies-heading">
              <RailHead id="companies-heading" title="企業から探す" />
              <ul className="grid grid-cols-2 gap-2">
                {companies.map(({ company, count }) => (
                  <li key={company.slug}>
                    <Link
                      href={`/companies/${company.slug}`}
                      className="flex h-full items-center gap-1.5 rounded-xl border border-line px-2.5 py-2.5 text-meta font-bold text-ink transition-colors hover:border-ink"
                    >
                      <CompanyMark company={company} size={18} />
                      {/* 狭い右の列で「Huggin…」「Anthro…」と切れていたので、切らずに2行まで折り返す（2026-11-03 の画面の点検） */}
                      <span className="line-clamp-2 min-w-0 leading-tight [overflow-wrap:anywhere]">{company.name}</span>
                      <span className="index-num ml-auto pl-0.5 text-meta font-normal text-muted">{count}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>

            {tags.length > 0 && (
              <section aria-labelledby="topics-heading">
                <RailHead id="topics-heading" title="トピック" />
                <div className="flex flex-wrap gap-1.5">
                  {tags.map(([slug, tag]) => (
                    <Link
                      key={slug}
                      href={`/tags/${encodeURIComponent(slug)}`}
                      className="rounded-md bg-surface px-3 py-1.5 text-meta font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink"
                    >
                      {tag.label}
                    </Link>
                  ))}
                </div>
              </section>
            )}
          </aside>
        </div>

        <NewsHub articles={broad.slice(0, NEWS_SIZE)} genres={broadGenres} />

        <footer className="mt-20 grid gap-3 border-t border-line pt-8">
          <p className="max-w-[70ch] text-desc leading-relaxed text-ink-soft">
            <strong>{siteConfig.siteName}（{siteConfig.nickname}）</strong>は、AI・テクノロジーを軸に、仕事とお金に関わるニュースを公式発表から最速で伝えるニュースメディアです。要点を3行で整理し、仕事やお金にどう活かせるか、明日から何ができるかまで書きます。{alsoCoveredSentence()}
          </p>
          <p className="flex flex-wrap gap-x-6 gap-y-2 text-note leading-relaxed text-ink-soft">
            <Link href="/about" className="underline decoration-line-strong underline-offset-4 hover:text-accent">サイソクについて・運営者情報</Link>
            <Link href="/editorial-policy" className="underline decoration-line-strong underline-offset-4 hover:text-accent">編集方針・AI利用と事実確認</Link>
            <Link href="/corrections" className="underline decoration-line-strong underline-offset-4 hover:text-accent">訂正履歴</Link>
          </p>
          {otherGenres.length > 0 && (
            <p className="text-meta text-muted">
              そのほかの分野：
              {otherGenres.map((genre, index) => (
                <span key={genre.slug}>
                  {index > 0 && "、"}
                  <Link href={`/genres/${genre.slug}`} className="underline underline-offset-4 hover:text-ink">
                    {genre.label}
                  </Link>
                </span>
              ))}
            </p>
          )}
        </footer>
      </div>
    </>
  );
}

function topTags(articles: ArticleSummary[], limit: number) {
  const counts = new Map<string, { label: string; count: number }>();
  for (const article of articles) {
    for (const tag of article.tags) {
      const slug = tagSlug(tag);
      const entry = counts.get(slug);
      if (entry) entry.count += 1;
      else counts.set(slug, { label: tag, count: 1 });
    }
  }
  return [...counts.entries()].sort((a, b) => b[1].count - a[1].count).slice(0, limit);
}

function RailHead({ id, title, note }: { id: string; title: string; note?: string }) {
  return (
    <div className="mb-4 border-b border-ink pb-2">
      <h2 id={id} className="t-h3">
        {title}
      </h2>
      {note && <p className="mt-0.5 text-meta text-muted">{note}</p>}
    </div>
  );
}

function RailRoundup({ article }: { article: ArticleSummary }) {
  return (
    <li>
      <Link href={`/articles/${article.slug}`} className="group grid grid-cols-[88px_minmax(0,1fr)] gap-3">
        <span className="relative aspect-video overflow-hidden rounded-lg bg-surface-2">
          <Thumb src={smallThumb(article.thumbnail, "mqdefault")} source={thumbSource(article)} cover={coverPath(article)} alt="" fill sizes="88px" className="object-cover" />
          <span className="index-num absolute bottom-1 left-1 rounded bg-ink/85 px-1.5 py-[1px] text-badge font-semibold text-paper">
            {article.sourceCount}本
          </span>
        </span>
        <span className="line-clamp-3 text-[15px] font-bold leading-[1.55] text-ink group-hover:underline">
          {article.seoTitle || article.title}
        </span>
      </Link>
    </li>
  );
}

function EmptyState() {
  return (
    <div className="mx-auto max-w-2xl px-5 py-32 text-center">
      <p className="eyebrow">NO ARTICLES YET</p>
      <p className="display mt-3 text-2xl">記事はまだありません</p>
      <p className="mt-4 text-desc leading-relaxed text-ink-soft">
        GitHub Actions が6時間ごとに新着動画を解析して、自動で記事を追加します。
      </p>
    </div>
  );
}
