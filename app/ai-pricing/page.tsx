import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import AssetShell, { Crumbs } from "@/components/assets/AssetShell";
import PricingBoard from "@/components/assets/PricingBoard";
import ShareLinks from "@/components/assets/ShareLinks";
import { formatDate } from "@/lib/articles";
import { formatMoney, getPricing, priceRows, type PriceRow } from "@/lib/pricing";
import { getAllWeeks } from "@/lib/weekly";
import { siteConfig } from "@/site.config";

const TITLE = "AIの料金比較・早見表【最新】ChatGPT・Claude・Gemini・APIの値段と日本で使えるか";
const DESCRIPTION =
  "ChatGPT・Claude・GeminiなどのAI APIの料金（100万トークンあたり）と月額プランを1ページで比較。日本で使えるか、無料で試せるかも一緒にわかります。チャットや要約などの使い方を選ぶと、月の費用が円で出ます。数字はすべて出典の記事で確認しています。";

export const metadata: Metadata = withPageShare("ai-pricing", "/ai-pricing", {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/ai-pricing" },
}, "AIの料金・日本対応 早見表");

/** 計算機の初期値と同じ使い方（入力100万・出力30万トークン/月）での月額 */
const defaultCost = (row: PriceRow) =>
  row.input === null || row.output === null ? null : row.input * 1 + row.output * 0.3;

function cheapestApi(rows: PriceRow[]): PriceRow | undefined {
  return rows
    .filter((row) => row.kind === "api" && !row.legacy && defaultCost(row) !== null)
    .sort((a, b) => defaultCost(a)! - defaultCost(b)!)[0];
}

function apiLine(row: PriceRow): string {
  const parts = [
    row.input !== null ? `入力${formatMoney(row.input, "USD")}` : "",
    row.output !== null ? `出力${formatMoney(row.output, "USD")}` : "",
  ].filter(Boolean);
  return parts.join("・");
}

/** AI料金・日本対応の早見表 */
export default function AiPricingPage() {
  const pricing = getPricing();
  const rows = priceRows();
  const apis = rows.filter((row) => row.kind === "api" && !row.legacy);
  const plans = rows.filter((row) => row.kind === "plan");
  const cheapest = cheapestApi(rows);
  const latestWeek = getAllWeeks()[0];
  const url = `${siteConfig.url}/ai-pricing`;

  const faq = [
    ...(cheapest
      ? [
          {
            q: "いちばん安いAIのAPIは？",
            a: `この表の中では${cheapest.product}（${cheapest.companyName}）が最も安く、100万トークンあたり${apiLine(cheapest)}です（${formatDate(cheapest.source.publishedAt)}の記事で確認）。`,
          },
        ]
      : []),
    ...plans.map((row) => ({
      q: `${row.product} ${row.plan}の料金はいくら？`,
      a: `月額${formatMoney(row.monthly ?? 0, row.currency)}です（${formatDate(row.source.publishedAt)}の記事で確認）。最新の金額は公式サイトで確かめてください。`,
    })),
    {
      q: "この表の料金はどこから集めている？",
      a: "サイソクが公開した記事に書かれた料金だけを使っています。記事の本文に同じ数字があるかを機械的に確かめ、同じモデルは新しい記事の数字を採っています。",
    },
  ];

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Dataset",
      name: "AIの料金・日本対応 早見表",
      description: DESCRIPTION,
      url,
      inLanguage: "ja",
      dateModified: pricing.updatedAt,
      isAccessibleForFree: true,
      creator: { "@type": "Organization", name: siteConfig.siteName, url: siteConfig.url },
      keywords: ["AI 料金", "API 料金", "ChatGPT 料金", "Claude 料金", "Gemini 料金", "100万トークン", "日本で使える"],
      variableMeasured: ["月額料金", "API入力料金（100万トークンあたり）", "API出力料金（100万トークンあたり）", "日本での提供状況", "無料で試せるか"],
      measurementTechnique: "サイソクの記事に書かれた料金を抽出し、記事本文に同じ数字があるかを機械的に確認",
      size: `${rows.length}件`,
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
        { "@type": "ListItem", position: 2, name: "AI料金・日本対応 早見表", item: url },
      ],
    },
  ];

  return (
    <AssetShell wide>
      <script type="application/ld+json" suppressHydrationWarning dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <Crumbs items={[{ href: "/", label: "ホーム" }, { label: "AI料金・日本対応 早見表" }]} />

      <header className="mt-5">
        <h1 className="t-h1 text-ink">AIの料金・日本対応 早見表</h1>
        <p className="t-lead mt-3">
          ChatGPT・Claude・GeminiなどのAIを使うと、月にいくらかかるか。使い方を選ぶだけで、円でわかります。日本で使えるかも一緒に見られます。
        </p>
        <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
          <span>
            最終更新 <span className="font-bold tabular-nums text-ink">{formatDate(pricing.updatedAt)}</span>
          </span>
          <span>
            <span className="tabular-nums">{apis.length}</span>モデル・<span className="tabular-nums">{plans.length}</span>プラン
          </span>
          <span>数字はすべてサイソクの記事で確かめたもの</span>
        </p>
      </header>

      <div className="mt-7">
        <PricingBoard rows={rows} />
      </div>

      <section aria-labelledby="method-heading" className="mt-16 max-w-[720px]">
        <H2 id="method-heading">この表の数字は信用できる？</H2>
        <ul className="grid gap-2.5 text-[14px] leading-[1.85] text-ink-soft">
          <li>・サイソクの記事に書かれた料金だけを載せています。</li>
          <li>・記事の本文に同じ数字があるかを、プログラムで1つずつ確かめています。</li>
          <li>・記事の中の「編集部注」や推定の数字は使いません。</li>
          <li>・同じモデルは、いちばん新しい記事の数字を採ります。</li>
          <li>・日本で使えるかは、そのモデルを扱った記事の判定を使います。</li>
          <li>・新しい記事に料金が出るたびに、自動で更新されます。</li>
        </ul>
        <p className="mt-4 rounded-xl bg-surface px-4 py-3 text-note leading-[1.8] text-muted">
          料金は変わることがあります。契約や開発の前に、必ず各社の公式の料金ページを確認してください。各行の「記事で確認」から、数字が書かれた記事を開けます。
          集め方は<Link href="/editorial-policy" className="underline underline-offset-2">編集方針</Link>にも書いています。
        </p>
      </section>

      <section aria-labelledby="faq-heading" className="mt-16 max-w-[720px]">
        <H2 id="faq-heading">よくある質問</H2>
        <div className="divide-y divide-line border-y border-line">
          {faq.map((item) => (
            <details key={item.q} className="group py-4">
              <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-[15px] font-bold text-ink">
                <span>{item.q}</span>
                <span aria-hidden className="mt-0.5 text-muted transition-transform group-open:rotate-45">
                  ＋
                </span>
              </summary>
              <p className="mt-2.5 text-[14px] leading-[1.85] text-ink-soft">{item.a}</p>
            </details>
          ))}
        </div>
      </section>

      <div className="mt-12 border-t border-line pt-6">
        <ShareLinks url={url} title="AIの料金・日本対応 早見表（サイソク）" />
      </div>

      <nav aria-label="あわせて見る" className="mt-8 grid gap-3 sm:grid-cols-3">
        {latestWeek && <MoreLink href={`/weekly/${latestWeek.id}`} title="今週のAIニュースまとめ" note="1週間の大事な10本を2行ずつ" />}
        <MoreLink href="/events" title="AIイベントの日程（日本時間）" note="基調講演の時刻と発表内容" />
        <MoreLink href="/usable" title="今すぐ使えるAI" note="日本から使える新機能の一覧" />
      </nav>
    </AssetShell>
  );
}

function H2({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} className="mb-5 text-balance text-[1.4rem] font-extrabold leading-snug text-ink sm:text-[1.65rem]">
      {children}
    </h2>
  );
}

function MoreLink({ href, title, note }: { href: string; title: string; note: string }) {
  return (
    <Link href={href} className="group rounded-xl border border-line p-4 transition-colors hover:border-ink">
      <span className="block text-[14.5px] font-bold text-ink group-hover:underline">{title} →</span>
      <span className="mt-1 block text-meta text-muted">{note}</span>
    </Link>
  );
}
