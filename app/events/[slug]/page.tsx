import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import AssetShell, { Crumbs, QHeading } from "@/components/assets/AssetShell";
import ShareLinks from "@/components/assets/ShareLinks";
import type { ArticleSummary } from "@/lib/articles";
import { companyOf, getCompany, OTHER } from "@/lib/companies";
import {
  daysUntil,
  eventArticles,
  eventLeadUp,
  eventStatus,
  formatJst,
  getAllEvents,
  getEvent,
  localDateRange,
  localDay,
  localTime,
  type TechEvent,
} from "@/lib/events";
import { siteConfig } from "@/site.config";
import { Phrases } from "@/lib/highlight";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllEvents().map((event) => ({ slug: event.slug }));
}

function titleOf(event: TechEvent): string {
  return eventStatus(event) === "upcoming"
    ? `${event.nameJa}はいつ？日本時間の日程と発表内容まとめ`
    : `${event.nameJa}の発表内容まとめ・日本時間の日程`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const event = getEvent(slug);
  if (!event) return {};
  const keynote = event.keynotes.find((k) => k.startUtc);
  const description = event.slug === "adobe-max-2026"
    ? "Adobe MAX 2026は米国マイアミで11月10〜12日開催。日本からのオンライン視聴と、別イベントAdobe MAX Japanの確認情報を整理。"
    : `${event.nameJa}は${localDateRange(event)}（現地時間）に${event.location.split(",")[0]}で開催。${
    keynote?.startUtc ? `基調講演は日本時間${formatJst(keynote.startUtc)}から。` : ""
  }公式に決まっていることと、発表内容をまとめます。`;
  return {
    title: titleOf(event),
    description,
    alternates: { canonical: `/events/${event.slug}` },
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale, type: "article", url: `/events/${event.slug}`, title: titleOf(event), description },
  };
}

const day = new Intl.DateTimeFormat("ja-JP", { month: "numeric", day: "numeric", weekday: "short", timeZone: "Asia/Tokyo" });

/** イベント速報ページ */
export default async function EventPage({ params }: Props) {
  const { slug } = await params;
  const event = getEvent(slug);
  if (!event) notFound();

  const status = eventStatus(event);
  const company = getCompany(event.companySlug);
  const color = company && company.slug !== OTHER.slug ? company.color : OTHER.color;
  const articles = eventArticles(event);
  const leadUp = status === "upcoming" ? eventLeadUp(event) : [];
  const recap = (event.recap ?? []).filter((row) => articles.some((a) => a.slug === row.slug));
  const others = getAllEvents()
    .filter((e) => e.slug !== event.slug && eventStatus(e) !== "ended")
    .slice(0, 5);
  const url = `${siteConfig.url}/events/${event.slug}`;
  const firstKeynote = event.keynotes.find((k) => k.startUtc);

  const faq = [
    { q: `${event.nameJa}はいつ？`, a: `${localDateRange(event)}（現地時間）に、${event.location}で開かれます。` },
    {
      q: `${event.nameJa}の基調講演は日本時間で何時？`,
      a: firstKeynote?.startUtc
        ? `${firstKeynote.title}は日本時間で${formatJst(firstKeynote.startUtc)}からです（現地時間 ${localDay(firstKeynote.startLocal)} ${localTime(firstKeynote.startLocal)}）。`
        : `基調講演の開始時刻は、${event.verifiedAt.replaceAll("-", "/")}時点の確認情報にありません。`,
    },
    ...(event.livestream
      ? [{ q: `${event.nameJa}はどこで見られる？`, a: "公式サイトでオンライン配信が案内されています。視聴の条件は公式サイトで確認してください。" }]
      : []),
  ];

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Event",
      name: event.name,
      alternateName: event.nameJa,
      description: event.summary,
      startDate: event.startDate,
      endDate: event.endDate,
      eventStatus: "https://schema.org/EventScheduled",
      eventAttendanceMode: event.livestream
        ? "https://schema.org/MixedEventAttendanceMode"
        : "https://schema.org/OfflineEventAttendanceMode",
      location: [
        { "@type": "Place", name: event.location, address: event.location },
        ...(event.livestream ? [{ "@type": "VirtualLocation", url: event.livestream }] : []),
      ],
      organizer: { "@type": "Organization", name: event.organizer, ...(event.officialUrl ? { url: event.officialUrl } : {}) },
      ...(event.officialUrl ? { url: event.officialUrl } : {}),
      mainEntityOfPage: url,
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: faq.map((item) => ({ "@type": "Question", name: item.q, acceptedAnswer: { "@type": "Answer", text: item.a } })),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: "AIイベント", item: `${siteConfig.url}/events` },
        { "@type": "ListItem", position: 3, name: event.nameJa, item: url },
      ],
    },
  ];

  return (
    <AssetShell>
      <script type="application/ld+json" suppressHydrationWarning dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <Crumbs items={[{ href: "/", label: "ホーム" }, { href: "/events", label: "AIイベント" }, { label: event.nameJa }]} />

      <header className="mt-5">
        <StatusLine event={event} />
        <h1 className="t-h1 mt-2"><Phrases text={titleOf(event)} /></h1>
        <p className="mt-3 text-desc leading-[1.85] text-ink-soft">{event.summary}</p>
      </header>

      {/* 基本情報 */}
      <dl
        className="mt-6 grid gap-x-6 rounded-xl border border-line p-4 text-note sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:p-5"
        style={{ borderLeft: `4px solid ${color}` }}
      >
        <Fact label="会期（現地）">{localDateRange(event)}</Fact>
        <Fact label="会場">{event.location}</Fact>
        <Fact label="主催">{event.organizer}</Fact>
        <Fact label="基調講演">
          {firstKeynote?.startUtc ? (
            <>
              日本時間 <strong className="font-bold tabular-nums">{formatJst(firstKeynote.startUtc)}</strong>
              <span className="text-muted">〜</span>
            </>
          ) : (
            <span className="text-muted">日時は未発表</span>
          )}
        </Fact>
        <Fact label="公式サイト">
          {event.officialUrl ? (
            <a href={event.officialUrl} target="_blank" rel="noopener noreferrer" className="break-all underline underline-offset-2 hover:text-ink">
              {event.officialUrl.replace(/^https?:\/\//, "").replace(/\/$/, "")}
            </a>
          ) : (
            <span className="text-muted">なし（報道で確認）</span>
          )}
        </Fact>
      </dl>

      {status !== "upcoming" && (
        <section aria-labelledby="recap-heading" className="mt-12">
          <QHeading
            id="recap-heading"
            note={status === "live" ? "開催中です。サイソクの記事が出るたびに、このページに自動で追加されます。" : "サイソクの記事から、発表された内容をまとめました。"}
          >
            何が発表された？
          </QHeading>
          {recap.length > 0 && (
            <ul className="mb-6 grid gap-2">
              {recap.map((row) => (
                <li key={row.text} className="grid grid-cols-[0.9rem_minmax(0,1fr)] gap-2 text-desc leading-[1.75] text-ink">
                  <span aria-hidden className="mt-[0.7em] size-[6px] rounded-full" style={{ background: color }} />
                  <span>
                    {row.text}
                    <Link href={`/articles/${row.slug}`} className="ml-2 text-meta text-muted underline underline-offset-2 hover:text-ink">
                      記事
                    </Link>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {articles.length > 0 ? (
            <ArticleList articles={articles} />
          ) : (
            <p className="rounded-xl bg-surface px-4 py-3 text-note text-muted">まだ記事はありません。発表があり次第、ここに並びます。</p>
          )}
        </section>
      )}

      {status === "upcoming" && (
        <section aria-labelledby="expect-heading" className="mt-12">
          <QHeading id="expect-heading" note="公式サイトなどで確認できたことだけを書いています。予想や噂は載せません。">
            何が決まっている？
          </QHeading>
          <ul className="grid gap-2">
            {event.confirmed.map((line) => (
              <li key={line} className="grid grid-cols-[0.9rem_minmax(0,1fr)] gap-2 text-desc leading-[1.75] text-ink">
                <span aria-hidden className="mt-[0.7em] size-[6px] rounded-full" style={{ background: color }} />
                <span>{line}</span>
              </li>
            ))}
          </ul>
          <p className="mt-4 rounded-xl bg-surface px-4 py-3 text-desc leading-[1.8] text-ink-soft">
            発表の中身は、事前には公表されていません。開催が始まると、サイソクの記事がこのページに自動で並びます。
          </p>
        </section>
      )}

      {event.slug === "adobe-max-2026" && (
        <section aria-labelledby="adobe-japan-heading" className="mt-12">
          <QHeading id="adobe-japan-heading">Adobe MAX 2026は日本で開催される？</QHeading>
          <p className="text-desc leading-[1.8] text-ink-soft">
            Adobeの案内では、2026年のこのイベントの会場は米国マイアミビーチです。オンライン版も案内され、公式FAQでは無料とされています。日本時間の各配信時刻や言語は、参加前に公式ページで確認してください。
          </p>
          <p className="mt-3 text-desc leading-[1.8] text-ink-soft">
            「Adobe MAX Japan」は別の国内イベント名です。今回確認したAdobeのイベント案内では、2026年の日本開催日を確認できませんでした（2026年10月31日確認）。<a href="https://maxjapan.adobe.com/" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">日本版の公式ページ</a>には、2025年の東京開催が掲載されています。開催しないと断定するものではありません。
          </p>
          <p className="mt-3 text-note leading-[1.8] text-muted">
            出典：<a href="https://www.adobe.com/jp/events.html" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">Adobeのイベント案内</a>、<a href="https://www.max.adobe.com/faq/" target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">Adobe MAX公式FAQ</a>
          </p>
        </section>
      )}

      <section aria-labelledby="keynote-heading" className="mt-12">
        <QHeading id="keynote-heading">基調講演は日本時間で何時？</QHeading>
        {event.keynotes.length > 0 ? (
          <ul className="divide-y divide-line border-y border-line">
            {event.keynotes.map((k) => (
              <li key={`${k.title}${k.startLocal}`} className="grid gap-1 py-3.5 sm:grid-cols-[11rem_minmax(0,1fr)] sm:gap-4">
                <span className="text-[15px] font-bold tabular-nums text-ink">{k.startUtc ? formatJst(k.startUtc) : "時刻未定"}</span>
                <span className="min-w-0 text-desc leading-[1.7] text-ink-soft">
                  {k.title}
                  {k.speaker && <span className="block text-meta text-muted">登壇: {k.speaker}</span>}
                  <span className="block text-meta text-muted">
                    現地時間 {localDay(k.startLocal)} {localTime(k.startLocal)}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-desc leading-[1.8] text-ink-soft">
            基調講演の開始時刻は、{event.verifiedAt.replaceAll("-", "/")}時点の確認情報にありません。時刻が分かり次第、日本時間で載せます。
          </p>
        )}
      </section>

      <section aria-labelledby="watch-heading" className="mt-12">
        <QHeading id="watch-heading">どこで見られる？</QHeading>
        {event.livestream ? (
          <p className="text-desc leading-[1.8] text-ink-soft">
            公式サイトでオンライン配信が案内されています。
            <a href={event.livestream} target="_blank" rel="noopener noreferrer" className="ml-1 font-bold text-ink underline underline-offset-2">
              公式の配信ページ
            </a>
          </p>
        ) : (
          <p className="text-desc leading-[1.8] text-ink-soft">
            オンライン配信は、確認した時点では公式に案内されていません。開催後はこのページで発表内容を日本語で読めます。
          </p>
        )}
      </section>

      {leadUp.length > 0 && (
        <section aria-labelledby="lead-heading" className="mt-12">
          <QHeading id="lead-heading" note="開催前45日に出た関連ニュースです。">
            直前に何があった？
          </QHeading>
          <ArticleList articles={leadUp} />
        </section>
      )}

      <section aria-labelledby="source-heading" className="mt-12">
        <QHeading id="source-heading">情報源は？</QHeading>
        <ul className="grid gap-1.5 text-note">
          {event.sources.map((source) => (
            <li key={source.url}>
              <a href={source.url} target="_blank" rel="noopener noreferrer" className="break-all text-ink-soft underline underline-offset-2 hover:text-ink">
                {source.title}
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-3 text-note leading-[1.8] text-muted">
          日程は{event.verifiedAt.replaceAll("-", "/")}に{event.confidence === "official" ? "公式サイトで" : "報道で"}確認しました。変更されることがあるので、参加する場合は公式サイトも確認してください。
        </p>
      </section>

      {others.length > 0 && (
        <section aria-labelledby="others-heading" className="mt-12">
          <QHeading id="others-heading">ほかにどんなイベントがある？</QHeading>
          <ul className="grid gap-2">
            {others.map((e) => (
              <li key={e.slug}>
                <Link href={`/events/${e.slug}`} className="group flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-lg border border-line px-4 py-3 hover:border-ink">
                  <span className="text-meta tabular-nums text-muted">{localDateRange(e)}</span>
                  <span className="text-[15px] font-bold text-ink group-hover:underline">{e.nameJa}</span>
                </Link>
              </li>
            ))}
          </ul>
          <Link href="/events" className="mt-4 inline-block text-note font-bold text-ink-soft hover:text-ink hover:underline">
            イベントの一覧へ →
          </Link>
        </section>
      )}

      <div className="mt-12 border-t border-line pt-6">
        <ShareLinks url={url} title={titleOf(event)} />
      </div>
    </AssetShell>
  );
}

function StatusLine({ event }: { event: TechEvent }) {
  const status = eventStatus(event);
  if (status === "live") {
    return (
      <p className="flex items-center gap-2 text-meta font-bold text-live">
        <span aria-hidden className="size-2 rounded-full bg-live" />
        開催中
      </p>
    );
  }
  if (status === "ended") return <p className="text-meta font-bold text-muted">終了しました</p>;
  const days = daysUntil(event);
  return (
    <p className="text-meta font-bold text-ink-soft">
      開催まで あと<span className="mx-0.5 tabular-nums">{days}</span>日
    </p>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="pt-2.5 text-meta font-bold text-muted sm:py-2">{label}</dt>
      <dd className="pb-2.5 text-ink sm:py-2">{children}</dd>
    </>
  );
}

function ArticleList({ articles }: { articles: ArticleSummary[] }) {
  return (
    <ul className="divide-y divide-line border-y border-line">
      {articles.slice(0, 30).map((article) => {
        const company = companyOf(article);
        return (
          <li key={article.slug} className="py-3.5">
            <p className="flex items-center gap-2 text-meta text-muted">
              <span className="font-bold text-ink">
                {company.slug === OTHER.slug ? article.sourceName || article.channel : company.name}
              </span>
              <span className="tabular-nums">{day.format(Date.parse(article.generatedAt || article.publishedAt))}</span>
            </p>
            <Link href={`/articles/${article.slug}`} className="mt-1 block text-[15.5px] font-bold leading-snug text-ink hover:underline">
              {article.seoTitle || article.title}
            </Link>
            {article.highlights[0] && <p className="mt-1 line-clamp-2 text-desc leading-[1.75] text-ink-soft">{article.highlights[0]}</p>}
          </li>
        );
      })}
    </ul>
  );
}
