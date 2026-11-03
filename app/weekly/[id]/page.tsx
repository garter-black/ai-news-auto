import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import AssetShell, { Crumbs, QHeading } from "@/components/assets/AssetShell";
import ShareLinks from "@/components/assets/ShareLinks";
import { coverPath, getArticleSummary } from "@/lib/articles";
import { COMPANIES, OTHER } from "@/lib/companies";
import { getAllEvents, localDateRange } from "@/lib/events";
import { getAllWeeks, getWeek, neighborWeeks, weekPublishedAt, weekRangeLabel, type WeeklyItem } from "@/lib/weekly";
import { siteConfig } from "@/site.config";
import { publisherJsonLd } from "@/lib/publisher";
import { Phrases } from "@/lib/highlight";

type Props = { params: Promise<{ id: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllWeeks().map((week) => ({ id: week.id }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const week = getWeek(id);
  if (!week) return {};
  const title = `今週のAIニュースまとめ（${weekRangeLabel(week)}）重要な10本を2行で`;
  const description = `${weekRangeLabel(week)}のAIニュースから重要な10本を選び、何が起きたか・なぜ大事かを2行ずつまとめました。${week.items
    .slice(0, 3)
    .map((item) => item.title.split(/[｜|：:]/)[0])
    .join("、")}など。`;
  const first = getArticleSummary(week.items[0]?.slug ?? "");
  const cover = first ? coverPath(first) : null;
  return {
    title,
    description,
    alternates: { canonical: `/weekly/${week.id}` },
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale,
      type: "article",
      url: `/weekly/${week.id}`,
      title: week.title,
      description,
      publishedTime: weekPublishedAt(week),
      ...(cover ? { images: [{ url: cover, width: 1200, height: 630 }] } : {}),
    },
  };
}

const companyColor = (name: string) => COMPANIES.find((c) => c.name === name)?.color ?? OTHER.color;
const day = new Intl.DateTimeFormat("ja-JP", { month: "numeric", day: "numeric", weekday: "short", timeZone: "Asia/Tokyo" });

/** 今週のAIニュースまとめ（1週分） */
export default async function WeeklyPage({ params }: Props) {
  const { id } = await params;
  const week = getWeek(id);
  if (!week) notFound();

  const { older, newer } = neighborWeeks(week.id);
  const published = weekPublishedAt(week);
  const top = week.items.slice(0, 3);
  const rest = week.items.slice(3);
  // 翌週（公開日から10日以内）に始まるイベント
  const from = Date.parse(published);
  const upcoming = getAllEvents().filter((e) => {
    const start = Date.parse(e.startUtc);
    return start >= from - 86_400_000 && start <= from + 10 * 86_400_000;
  });
  const url = `${siteConfig.url}/weekly/${week.id}`;

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "NewsArticle",
      headline: week.title,
      description: week.lede.join(""),
      datePublished: published,
      dateModified: week.generatedAt,
      inLanguage: "ja",
      mainEntityOfPage: url,
      author: { "@type": "Organization", name: siteConfig.author, url: `${siteConfig.url}/about` },
      publisher: publisherJsonLd,
    },
    {
      "@context": "https://schema.org",
      "@type": "ItemList",
      name: week.title,
      numberOfItems: week.items.length,
      itemListElement: week.items.map((item, index) => ({
        "@type": "ListItem",
        position: index + 1,
        url: `${siteConfig.url}/articles/${item.slug}`,
        name: item.title,
      })),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: "今週のAIニュース", item: `${siteConfig.url}/weekly` },
        { "@type": "ListItem", position: 3, name: weekRangeLabel(week), item: url },
      ],
    },
  ];

  return (
    <AssetShell>
      <script type="application/ld+json" suppressHydrationWarning dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <Crumbs items={[{ href: "/", label: "ホーム" }, { href: "/weekly", label: "今週のAIニュース" }, { label: weekRangeLabel(week) }]} />

      <header className="mt-5">
        <p className="text-meta font-bold text-muted">
          毎週月曜 7時更新 ・ <span className="tabular-nums">{week.id.replace("-", "年 第")}週</span>
        </p>
        <h1 className="t-h1 mt-2"><Phrases text={week.title} /></h1>
        <div className="mt-4 grid gap-1.5 text-desc leading-[1.85] text-ink-soft">
          {week.lede.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
        <p className="mt-3 text-meta text-muted">
          この週にサイソクが出したAIの記事 <span className="tabular-nums">{week.articleCount}</span>本から選びました。
        </p>
      </header>

      <section aria-labelledby="top-heading" className="mt-10">
        <QHeading id="top-heading">今週いちばん大きなニュースは？</QHeading>
        <ol className="grid gap-4">
          {top.map((item, index) => (
            <Item key={item.slug} item={item} rank={index + 1} large />
          ))}
        </ol>
      </section>

      {rest.length > 0 && (
        <section aria-labelledby="rest-heading" className="mt-12">
          <QHeading id="rest-heading">ほかに押さえておきたいニュースは？</QHeading>
          <ol className="grid gap-3">
            {rest.map((item, index) => (
              <Item key={item.slug} item={item} rank={index + 4} />
            ))}
          </ol>
        </section>
      )}

      {upcoming.length > 0 && (
        <section aria-labelledby="next-heading" className="mt-12">
          <QHeading id="next-heading" note="日付は現地時間。基調講演の日本時間と、開催後の発表内容はイベントのページで見られます。">
            次の週は何がある？
          </QHeading>
          <ul className="grid gap-2">
            {upcoming.map((event) => (
              <li key={event.slug}>
                <Link href={`/events/${event.slug}`} className="group flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border border-line px-4 py-3 hover:border-ink">
                  <span className="text-meta tabular-nums text-muted">{localDateRange(event)}</span>
                  <span className="text-[15px] font-bold text-ink group-hover:underline">{event.nameJa}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="mt-12 grid gap-4 border-t border-line pt-6">
        <ShareLinks url={url} title={week.title} />
        <nav aria-label="ほかの週" className="flex flex-wrap justify-between gap-3 text-note">
          {older ? (
            <Link href={`/weekly/${older.id}`} className="font-bold text-ink-soft hover:text-ink hover:underline">
              ← 前の週（{weekRangeLabel(older)}）
            </Link>
          ) : (
            <span />
          )}
          {newer ? (
            <Link href={`/weekly/${newer.id}`} className="font-bold text-ink-soft hover:text-ink hover:underline">
              次の週（{weekRangeLabel(newer)}）→
            </Link>
          ) : (
            <Link href="/weekly" className="font-bold text-ink-soft hover:text-ink hover:underline">
              これまでのまとめ →
            </Link>
          )}
        </nav>
        <p className="text-meta text-muted">
          AIの料金を比べるなら <Link href="/ai-pricing" className="underline underline-offset-2 hover:text-ink">AIの料金・日本対応 早見表</Link>
        </p>
      </div>
    </AssetShell>
  );
}

function Item({ item, rank, large = false }: { item: WeeklyItem; rank: number; large?: boolean }) {
  const color = companyColor(item.company);
  return (
    <li
      className={`grid grid-cols-[2rem_minmax(0,1fr)] gap-3 rounded-xl border border-line ${large ? "p-4 sm:p-5" : "px-4 py-3.5"}`}
      style={{ borderLeft: `4px solid ${color}` }}
    >
      <span className={`tabular-nums font-bold text-muted ${large ? "text-[1.4rem] leading-none" : "text-[1.05rem] leading-tight"}`}>{rank}</span>
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-x-2 text-meta text-muted">
          {item.company && (
            <span className="font-bold text-ink">
              {item.company}
            </span>
          )}
          <span className="tabular-nums">{day.format(Date.parse(item.publishedAt))}</span>
        </p>
        <Link href={`/articles/${item.slug}`} className={`mt-1 block font-bold leading-snug text-ink hover:underline ${large ? "text-[17px]" : "text-[15.5px]"}`}>
          {item.title}
        </Link>
        <p className="mt-2 text-desc leading-[1.8] text-ink">{item.what}</p>
        {item.why && (
          <p className="mt-1 text-desc leading-[1.8] text-ink-soft">
            <span className="mr-1.5 text-meta font-bold text-muted">なぜ大事？</span>
            {item.why}
          </p>
        )}
      </div>
    </li>
  );
}
