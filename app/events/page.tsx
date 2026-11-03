import type { Metadata } from "next";
import Link from "next/link";
import AssetShell, { Crumbs, QHeading } from "@/components/assets/AssetShell";
import { getCompany, OTHER } from "@/lib/companies";
import { daysUntil, eventArticles, eventStatus, formatJst, getAllEvents, localDateRange, type TechEvent } from "@/lib/events";
import { siteConfig } from "@/site.config";

const DESCRIPTION =
  "AWS re:Invent、Microsoft Ignite、NVIDIA GTCなど、AI・テックの主要イベントの日程を日本時間でまとめています。基調講演の時刻、公式に決まっていること、開催後の発表内容まで1ページずつ。";

function monthsLabel(events: TechEvent[]): string {
  const months = [...new Set(events.slice(0, 8).map((e) => Number(e.startDate.split("-")[1])))].slice(0, 3);
  return months.length ? `【${months.map((m) => `${m}月`).join("・")}】` : "";
}

export function generateMetadata(): Metadata {
  const upcoming = getAllEvents().filter((e) => eventStatus(e) !== "ended");
  const title = `AI・テックイベントの日程${monthsLabel(upcoming)}日本時間の基調講演と発表内容`;
  return {
    title,
    description: DESCRIPTION,
    alternates: { canonical: "/events" },
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale, type: "website", url: "/events", title: "AI・テックイベントの日程（日本時間）", description: DESCRIPTION },
  };
}

/** イベントの一覧（これから / 終わった） */
export default function EventsPage() {
  const all = getAllEvents();
  const upcoming = all.filter((e) => eventStatus(e) !== "ended");
  const ended = all.filter((e) => eventStatus(e) === "ended").reverse();

  // 月ごとにまとめる（現地の開始月）
  const months: { label: string; events: TechEvent[] }[] = [];
  for (const event of upcoming) {
    const [y, m] = event.startDate.split("-").map(Number);
    const label = `${y}年${m}月`;
    const last = months.at(-1);
    if (last?.label === label) last.events.push(event);
    else months.push({ label, events: [event] });
  }

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: "AI・テックイベントの日程",
    itemListElement: upcoming.map((event, index) => ({
      "@type": "ListItem",
      position: index + 1,
      url: `${siteConfig.url}/events/${event.slug}`,
      name: event.nameJa,
    })),
  };

  return (
    <AssetShell>
      <script type="application/ld+json" suppressHydrationWarning dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <Crumbs items={[{ href: "/", label: "ホーム" }, { label: "AIイベント" }]} />
      <h1 className="t-h1 mt-5">AI・テックイベントの日程</h1>
      <p className="t-lead mt-3">
        主要なAI・テックイベントを、日本時間の基調講演とあわせてまとめています。公式に決まったことだけを載せ、開催後は発表内容を自動で追加します。
      </p>

      {months.map(({ label, events }) => (
        <section key={label} aria-labelledby={`m-${label}`} className="mt-10">
          <h2 id={`m-${label}`} className="t-h3 mb-3 border-b border-line-strong pb-2">
            {label}
          </h2>
          <ul className="grid gap-3">
            {events.map((event) => (
              <EventRow key={event.slug} event={event} />
            ))}
          </ul>
        </section>
      ))}

      {ended.length > 0 && (
        <section aria-labelledby="ended-heading" className="mt-14">
          <QHeading id="ended-heading">終わったイベントで何が発表された？</QHeading>
          <ul className="grid gap-3">
            {ended.map((event) => (
              <EventRow key={event.slug} event={event} />
            ))}
          </ul>
        </section>
      )}
    </AssetShell>
  );
}

function EventRow({ event }: { event: TechEvent }) {
  const status = eventStatus(event);
  const company = getCompany(event.companySlug);
  const color = company && company.slug !== OTHER.slug ? company.color : OTHER.color;
  const keynote = event.keynotes.find((k) => k.startUtc);
  const count = eventArticles(event).length;
  return (
    <li>
      <Link
        href={`/events/${event.slug}`}
        className="group grid gap-1 rounded-xl border border-line px-4 py-3.5 transition-colors hover:border-ink sm:grid-cols-[10.5rem_minmax(0,1fr)_auto] sm:items-center sm:gap-4"
        style={{ borderLeft: `4px solid ${color}` }}
      >
        <span className="text-meta tabular-nums text-ink-soft">{localDateRange(event).replace(/^\d{4}年/, "")}</span>
        <span className="min-w-0">
          <span className="block text-[16px] font-bold leading-snug text-ink group-hover:underline">{event.nameJa}</span>
          <span className="mt-0.5 block text-meta text-muted">
            {event.location.split(",")[0]}
            {keynote?.startUtc && <> ・ 基調講演 日本時間 {formatJst(keynote.startUtc)}</>}
          </span>
        </span>
        <span className="text-meta font-bold">
          {status === "live" ? (
            <span className="text-live">開催中</span>
          ) : status === "ended" ? (
            <span className="text-muted">{count > 0 ? `記事 ${count}本` : "終了"}</span>
          ) : (
            <span className="text-ink-soft">あと{daysUntil(event)}日</span>
          )}
        </span>
      </Link>
    </li>
  );
}
