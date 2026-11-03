import Link from "next/link";
import { Suspense } from "react";
import DifyA8Attribution from "@/components/DifyA8Attribution";
import ArticleCard from "@/components/ArticleCard";
import AssetShell, { Crumbs, QHeading } from "@/components/assets/AssetShell";
import { Pixels } from "@/components/rail/parts";
import { formatDate, type ArticleSummary } from "@/lib/articles";
import { AFFILIATE_REL } from "@/lib/affiliate";
import type { ChooseGuide, Cell } from "@/lib/choose-data";
import type { ResolvedService } from "@/lib/choose";
import { Phrases } from "@/lib/highlight";

/**
 * 選び方ガイドの本体。見た目は早見表（/ai-pricing）と同じ型にそろえ、広告らしい飾りは付けない。
 * 提携リンクも公式サイトへの普通のリンクも、同じ見た目の文字リンク（表の中に1か所だけ）。
 */
type Props = {
  guide: ChooseGuide;
  services: ResolvedService[];
  related: ArticleSummary[];
  others: { href: string; title: string; note: string }[];
  jsonLd: object[];
};

const TONE_CLASS: Record<string, string> = {
  good: "bg-go-soft",
  bad: "bg-signal-soft",
};

export default function ChooseView({ guide, services, related, others, jsonLd }: Props) {
  const byId = new Map(services.map((s) => [s.id, s]));
  const pixels = [...new Set(services.flatMap((s) => (s.pixel ? [s.pixel] : [])))];
  const hasSponsored = services.some((s) => s.sponsored);
  const checked = formatDate(guide.checkedAt);

  return (
    <AssetShell wide>
      {guide.slug === "dify-hosting" && <Suspense fallback={null}><DifyA8Attribution /></Suspense>}
      <script type="application/ld+json" suppressHydrationWarning dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <Pixels pixels={pixels} />
      <Crumbs items={[{ href: "/", label: "ホーム" }, { label: "選び方ガイド" }]} />

      <header className="mt-5 max-w-[880px]">
        <h1 className="t-h1 text-balance text-ink"><Phrases text={guide.h1} /></h1>
        <p className="t-lead mt-3">{guide.lead}</p>
        <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
          <span>
            最終更新 <span className="font-bold tabular-nums text-ink">{formatDate(guide.updatedAt)}</span>
          </span>
          <span>{services.length}件を比較</span>
          <span>料金・条件は{checked}に公式サイトで確認</span>
        </p>
        {hasSponsored && (
          <p className="mt-1.5 text-meta text-muted">
            このページには広告リンクを含みます（<a href="#about" className="underline underline-offset-2">詳しく</a>）
          </p>
        )}
      </header>

      {/* 使い方から考える */}
      <section aria-labelledby="needs-heading" className="mt-12 max-w-[860px]">
        <QHeading id="needs-heading">どんな使い方なら、何を選べばいい？</QHeading>
        <dl className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
          {guide.needs.map((need) => (
            <div key={need.title}>
              <dt className="text-[16px] font-bold leading-snug text-ink">{need.title}</dt>
              <dd className="mt-1.5 text-desc leading-[1.85] text-ink-soft">{need.body}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* 比較表 */}
      <section aria-labelledby="table-heading" className="mt-14">
        <QHeading
          id="table-heading"
          // ガイドごとの注記は guide.tableNote に置く。全部のガイドに共通の文だけを既定にする（「速度は公称値」が転職ガイドに出ていた。台帳 W13）
          note={guide.tableNote ?? `${checked}に各社の公式サイトで確認。`}
        >
          比較表：{services.length}件を同じ物差しで
        </QHeading>
        <Legend />
        <CompareTable guide={guide} byId={byId} />
        <CompareCards guide={guide} byId={byId} />
        <p className="mt-3 text-note leading-[1.8] text-muted">
          料金・特典は変わることがあります。申し込む前に、各社の公式サイトで最新の情報を確認してください。
        </p>
      </section>

      {/* こんな人には */}
      <section aria-labelledby="persona-heading" className="mt-14 max-w-[860px]">
        <QHeading id="persona-heading">迷ったら、こう選ぶ</QHeading>
        <ul className="divide-y divide-line border-y border-line">
          {guide.personas.map((persona) => (
            <li key={persona.who} className="py-4">
              <p className="text-[16px] font-bold leading-snug text-ink">{persona.who}なら</p>
              <p className="mt-1 text-desc leading-[1.85] text-ink-soft">{persona.why}</p>
              <p className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-note">
                {persona.picks.flatMap((id) => byId.get(id) ?? []).map((service) => (
                  <a key={service.id} href={`#svc-${service.id}`} className="font-semibold text-accent underline underline-offset-2">
                    {service.name}の注意点
                  </a>
                ))}
              </p>
            </li>
          ))}
        </ul>
      </section>

      {/* 選ぶときのポイント */}
      <section aria-labelledby="points-heading" className="mt-14 max-w-[860px]">
        <QHeading id="points-heading">申し込む前に確かめたいこと</QHeading>
        <ol className="grid gap-5">
          {guide.points.map((point, index) => (
            <li key={point.title} className="grid grid-cols-[2rem_1fr] gap-2">
              <span className="index-num text-[20px] font-bold leading-snug text-accent">{index + 1}</span>
              <div>
                <h3 className="text-[17px] font-bold leading-snug text-ink">{point.title}</h3>
                <p className="mt-1 text-desc leading-[1.85] text-ink-soft">{point.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* サービスごとの注意点 */}
      <section aria-labelledby="detail-heading" className="mt-14 max-w-[860px]">
        <QHeading id="detail-heading">サービスごとの注意点と出典</QHeading>
        <div className="grid gap-x-8 gap-y-7 sm:grid-cols-2">
          {services.map((service) => (
            <div key={service.id} id={`svc-${service.id}`} className="scroll-mt-24">
              <h3 className="text-[17px] font-bold leading-snug text-ink">
                {service.name}
                <span className="ml-2 text-meta font-normal text-muted">{service.kind}</span>
              </h3>
              <p className="mt-1 text-desc leading-[1.8] text-ink-soft">向いている人：{service.bestFor}</p>
              <ul className="mt-1.5 grid gap-1 text-note leading-[1.8] text-ink-soft">
                {service.cautions.map((text) => (
                  <li key={text} className="grid grid-cols-[1em_1fr] gap-1">
                    <span aria-hidden>・</span>
                    {text}
                  </li>
                ))}
              </ul>
              <p className="mt-1.5 text-note text-muted">
                出典（{checked}確認）：
                {service.sources.map((source, index) => (
                  <span key={source.url}>
                    {index > 0 && "／"}
                    <a href={source.url} target="_blank" rel="nofollow noopener" className="underline underline-offset-2 hover:text-accent">
                      {source.label}
                    </a>
                  </span>
                ))}
              </p>
            </div>
          ))}
        </div>
      </section>

      {guide.extraSources && (
        <p className="mt-6 text-note leading-relaxed text-muted">補足の出典：{guide.extraSources.map((source, i) => (
          <span key={source.url}>{i > 0 && " ／ "}<a href={source.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">{source.label}</a></span>
        ))}</p>
      )}

      <section aria-labelledby="faq-heading" className="mt-14 max-w-[760px]">
        <QHeading id="faq-heading">よくある質問</QHeading>
        <div className="divide-y divide-line border-y border-line">
          {guide.faq.map((item) => (
            <details key={item.q} className="group py-4">
              <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-[16px] font-bold text-ink">
                <span>{item.q}</span>
                <span aria-hidden className="mt-0.5 text-muted transition-transform group-open:rotate-45 motion-reduce:transition-none">
                  ＋
                </span>
              </summary>
              <p className="mt-2.5 text-desc leading-[1.85] text-ink-soft">{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      {related.length > 0 && (
        <section aria-labelledby="news-heading" className="mt-14">
          <QHeading id="news-heading">関連するニュース</QHeading>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {related.map((article) => (
              <ArticleCard key={article.slug} article={article} />
            ))}
          </div>
        </section>
      )}

      <section id="about" aria-labelledby="about-heading" className="mt-14 max-w-[760px] scroll-mt-24 border-t border-line pt-6">
        <h2 id="about-heading" className="t-h3 text-ink">
          このページについて
        </h2>
        <div className="mt-2 grid gap-2 text-note leading-[1.85] text-ink-soft">
          <p>
            料金・条件・特典は、{checked}に各社の公式サイトに書かれていた内容だけで作っています。記載が見つからなかった項目は「公式ページで確認」としています。編集部が実際に契約して試した結果ではありません。
          </p>
          {hasSponsored && (
            <p>
              一部のリンクは広告（アフィリエイト）リンクで、そこから申し込みがあると当サイトに紹介料が入ることがあります。広告かどうかで載せる順番や書き方は変えていません。広告でないサービスも同じように並べています。
            </p>
          )}
          <p>
            制作方法は<Link href="/editorial-policy" className="mx-1 text-accent underline underline-offset-2">編集方針</Link>、
            誤りのご指摘は<Link href="/contact" className="mx-1 text-accent underline underline-offset-2">お問い合わせ</Link>へ。
          </p>
        </div>
      </section>

      {others.length > 0 && (
        <nav aria-label="あわせて見る" className="mt-10 grid gap-3 sm:grid-cols-3">
          {others.map((item) => (
            <Link key={item.href} href={item.href} className="group rounded-xl border border-line p-4 transition-colors hover:border-ink">
              <span className="block text-[15px] font-bold text-ink group-hover:underline">{item.title} →</span>
              <span className="mt-1 block text-meta text-muted">{item.note}</span>
            </Link>
          ))}
        </nav>
      )}
    </AssetShell>
  );
}

/** 色の意味（色だけに頼らず、言葉でも示す） */
function Legend() {
  return (
    <p className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-meta text-ink-soft">
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className="inline-block size-3.5 rounded-sm border border-line bg-go-soft" />
        うれしい点
      </span>
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className="inline-block size-3.5 rounded-sm border border-line bg-signal-soft" />
        気をつける点
      </span>
      <span className="text-muted sm:hidden">サービスごとのカードで表示しています</span>
    </p>
  );
}

function toneLabel(cell?: Cell): string {
  return cell?.tone === "good" ? "（うれしい点）" : cell?.tone === "bad" ? "（気をつける点）" : "";
}

/** 公式サイトへのリンク。提携リンクも普通のリンクも見た目は同じ */
function OutLink({ service, dify = false, className = "" }: { service: ResolvedService; dify?: boolean; className?: string }) {
  return (
    <a
      href={service.href}
      data-funnel-choice={service.id}
      data-a8-dify={dify && service.id === "xserver-dify" && service.sponsored ? "v1" : undefined}
      target="_blank"
      rel={service.sponsored ? AFFILIATE_REL : "nofollow noopener"}
      className={`font-semibold text-accent underline underline-offset-2 ${className}`}
    >
      公式サイト{service.sponsored && "（広告）"}
      <span className="sr-only">（新しいタブで開きます）</span>
    </a>
  );
}

/** PC：種類ごとに区切った表。1列目（サービス名）は横にスクロールしても固定 */
function CompareTable({ guide, byId }: { guide: ChooseGuide; byId: Map<string, ResolvedService> }) {
  const colSpan = guide.columns.length + 1;
  return (
    <div
      className="relative hidden overflow-x-auto overscroll-x-contain rounded-xl border border-line sm:block"
      role="region"
      aria-label="比較表（横にスクロールできます）"
      tabIndex={0}
      data-aff-pos="choose-table"
    >
      <table className="w-full min-w-[980px] border-collapse text-left">
        <caption className="sr-only">{guide.h1}の比較表</caption>
        <thead>
          <tr className="border-b border-line-strong bg-surface">
            <th scope="col" className="sticky left-0 z-20 w-[11rem] min-w-[11rem] bg-surface px-3 py-2.5 text-meta font-bold text-ink">
              サービス
            </th>
            {guide.columns.map((col) => (
              <th key={col.key} scope="col" className="min-w-[8.25rem] px-3 py-2.5 text-meta font-bold text-ink">
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        {guide.groups.map((group) => (
          <tbody key={group.label}>
            <tr>
              <th scope="colgroup" colSpan={colSpan} className="bg-paper px-3 pb-1.5 pt-4 text-left text-meta font-bold text-accent">
                {group.label}
              </th>
            </tr>
            {group.ids.flatMap((id) => byId.get(id) ?? []).map((service) => (
              <tr key={service.id} className="border-t border-line align-top">
                <th scope="row" className="sticky left-0 z-10 w-[11rem] min-w-[11rem] border-r border-line bg-paper px-3 py-3 text-left">
                  <span className="block text-[15px] font-bold leading-snug text-ink">{service.name}</span>
                  <span className="mt-0.5 block text-meta font-normal text-muted">{service.kind}</span>
                  <OutLink service={service} dify={guide.slug === "dify-hosting"} className="mt-1.5 inline-block text-note" />
                </th>
                {guide.columns.map((col) => {
                  const cell = service.cells[col.key];
                  return (
                    <td
                      key={col.key}
                      data-dense
                      className={`px-3 py-3 text-note leading-[1.65] text-ink ${cell?.tone ? TONE_CLASS[cell.tone] : ""}`}
                    >
                      {cell?.text ?? "公式ページで確認"}
                      <span className="sr-only">{toneLabel(cell)}</span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}

/** スマホ：1サービス1枚のカード。項目は表と同じ順・同じ色 */
function CompareCards({ guide, byId }: { guide: ChooseGuide; byId: Map<string, ResolvedService> }) {
  return (
    <div className="grid gap-6 sm:hidden" data-aff-pos="choose-cards">
      {guide.groups.map((group) => (
        <div key={group.label}>
          <p className="mb-2 text-meta font-bold text-accent">{group.label}</p>
          <ul className="grid gap-3">
            {group.ids.flatMap((id) => byId.get(id) ?? []).map((service) => (
              <li key={service.id} className="overflow-hidden rounded-xl border border-line">
                <div className="flex items-baseline justify-between gap-3 border-b border-line bg-surface px-3.5 py-2.5">
                  <p className="min-w-0">
                    <span className="text-[16px] font-bold leading-snug text-ink">{service.name}</span>
                    <span className="ml-2 text-meta text-muted">{service.kind}</span>
                  </p>
                  <OutLink service={service} dify={guide.slug === "dify-hosting"} className="shrink-0 text-note" />
                </div>
                <dl className="divide-y divide-line">
                  {guide.columns.map((col) => {
                    const cell = service.cells[col.key];
                    return (
                      <div
                        key={col.key}
                        className={`grid grid-cols-[6.5rem_1fr] gap-2 px-3.5 py-2 ${cell?.tone ? TONE_CLASS[cell.tone] : ""}`}
                      >
                        <dt className="text-meta font-bold leading-[1.6] text-muted">{col.label}</dt>
                        <dd data-dense className="text-note leading-[1.6] text-ink">
                          {cell?.text ?? "公式ページで確認"}
                          <span className="sr-only">{toneLabel(cell)}</span>
                        </dd>
                      </div>
                    );
                  })}
                </dl>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
