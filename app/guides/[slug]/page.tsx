import { authorJsonLd, publisherJsonLd } from "@/lib/publisher";
import fs from "@/lib/content-fs";
import path from "node:path";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import AffiliateBox from "@/components/AffiliateBox";
import HowtoGuideView from "@/components/HowtoGuideView";
import CompanyMark from "@/components/CompanyMark";
import RelTime from "@/components/RelTime";
import Timeline from "@/components/Timeline";
import { coverPath, formatDate, type AffiliateLink } from "@/lib/articles";
import { getCompany, OTHER } from "@/lib/companies";
import { getAllGuides, getGuide, guideArticles } from "@/lib/guides";
import { Highlight } from "@/lib/highlight";
import { getAllHowtos, getHowto } from "@/lib/howto";
import { siteConfig } from "@/site.config";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;

// 製品ガイド（content/guides）と使い方ガイド（content/howto）が同じ /guides/<slug> を使う
export function generateStaticParams() {
  return [...getAllHowtos(), ...getAllGuides()].map((guide) => ({ slug: guide.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const howto = getHowto(slug);
  if (howto) {
    return {
      title: howto.seoTitle,
      description: howto.description,
      alternates: { canonical: `/guides/${howto.slug}` },
      openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale, type: "article", url: `/guides/${howto.slug}`, title: howto.title, description: howto.description },
    };
  }
  const guide = getGuide(slug);
  if (!guide) return {};
  const latest = guideArticles(guide)[0];
  const cover = latest ? coverPath(latest) : null;
  return {
    title: guide.seoTitle,
    description: guide.description,
    alternates: { canonical: `/guides/${guide.slug}` },
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale,
      type: "article",
      url: `/guides/${guide.slug}`,
      title: guide.title,
      description: guide.description,
      ...(cover ? { images: [{ url: cover, width: 1200, height: 630 }] } : {}),
    },
  };
}

/** affiliate_links.json から、ガイドで指定した広告だけを取り出す（未提携の仮リンクは除外） */
function guideAffiliates(names: string[]): AffiliateLink[] {
  if (names.length === 0) return [];
  try {
    const raw = JSON.parse(fs.readFileSync(path.join(process.cwd(), "affiliate_links.json"), "utf8"));
    return names.flatMap((name) => {
      const entry = raw[name];
      if (!entry || typeof entry !== "object" || String(entry.url).includes("YOUR_ID")) return [];
      return [{ name, label: entry.label ?? name, url: entry.url, note: entry.note, pixel: entry.pixel }];
    });
  } catch {
    return [];
  }
}

export default async function GuidePage({ params }: Props) {
  const { slug } = await params;
  const howto = getHowto(slug);
  if (howto) return <HowtoGuideView guide={howto} />;
  const guide = getGuide(slug);
  if (!guide) notFound();

  const articles = guideArticles(guide);
  const company = getCompany(guide.companySlug) ?? OTHER;
  const updatedAt = articles[0]?.publishedAt;
  const affiliates = guideAffiliates(guide.affiliates);

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: guide.title,
      description: guide.description,
      inLanguage: "ja",
      ...(updatedAt ? { dateModified: updatedAt } : {}),
      mainEntityOfPage: `${siteConfig.url}/guides/${guide.slug}`,
      author: authorJsonLd,
      publisher: publisherJsonLd,
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: guide.faq.map((item) => ({
        "@type": "Question",
        name: item.q,
        acceptedAnswer: { "@type": "Answer", text: item.a },
      })),
    },
  ];

  return (
    <div className="mx-auto max-w-[860px] px-5 py-8 sm:px-8 sm:py-12">
      <script
        type="application/ld+json"
        suppressHydrationWarning
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav aria-label="パンくず" className="flex flex-wrap items-center gap-1.5 text-meta text-muted">
        <Link href="/" className="hover:text-accent">ホーム</Link>
        <span aria-hidden>/</span>
        <Link href="/guides" className="hover:text-accent">製品ガイド</Link>
        <span aria-hidden>/</span>
        <span>{guide.name}</span>
      </nav>

      <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta">
        <span className="rounded-sm bg-ink px-2.5 py-0.5 text-badge font-bold tracking-wider text-paper">
          製品ガイド
        </span>
        {company.slug !== OTHER.slug && <CompanyMark company={company} size={20} withName />}
        {updatedAt && (
          <span className="text-muted">
            最終更新 <RelTime iso={updatedAt} className="index-num font-semibold text-ink" />
            <span className="index-num ml-1.5">（{formatDate(updatedAt)}）</span>
          </span>
        )}
      </div>

      <h1 className="t-h1 headline mt-3">
        <Highlight text={guide.title} />
      </h1>

      <section aria-labelledby="summary-heading" className="mt-6 rounded-2xl border-l-4 border-live bg-surface p-5 sm:p-6">
        <h2 id="summary-heading" className="t-h3">30秒でわかる{guide.name}</h2>
        <ol className="mt-4 grid gap-3.5">
          {guide.summary.map((point, index) => (
            <li key={index} className="grid grid-cols-[24px_1fr] gap-3 text-desc font-medium leading-[1.7] text-ink">
              <span className="index-num mt-0.5 grid size-6 place-items-center rounded-sm bg-ink text-meta font-semibold text-paper">
                {index + 1}
              </span>
              {point}
            </li>
          ))}
        </ol>
      </section>

      <p className="mt-6 text-desc leading-[1.95] text-ink-soft">{guide.intro}</p>
      <p className="mt-3 inline-block rounded-sm bg-surface px-2 py-0.5 text-badge font-bold text-muted">
        本ページはプロモーションを含みます
      </p>

      <Section id="features" title={`${guide.name}でできること`}>
        <div className="grid gap-3 sm:grid-cols-2">
          {guide.features.map((feature) => (
            <div
              key={feature.title}
              className="rounded-2xl border border-line p-5 sm:[&:last-child:nth-child(odd)]:col-span-2"
            >
              <h3 className="t-card">{feature.title}</h3>
              <p className="mt-2 text-desc leading-[1.85] text-ink-soft">{feature.body}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section id="pricing" title="料金・提供状況">
        <div className="overflow-hidden rounded-2xl border border-line">
          <table className="w-full text-left text-note">
            <tbody>
              {guide.pricing.map((row) => (
                <tr key={row.label} className="border-b border-line last:border-0">
                  <th scope="row" className="w-[30%] bg-surface px-4 py-3.5 align-top font-bold text-ink">
                    {row.label}
                  </th>
                  <td className="px-4 py-3.5 align-top">
                    <span className="font-bold text-ink">{row.value}</span>
                    {row.note && <span className="mt-1 block text-meta text-muted">{row.note}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-note leading-relaxed text-muted">
          料金や提供条件は変わることがあります。契約前に必ず公式サイトで最新情報を確認してください。
        </p>
      </Section>

      <Section id="howto" title="始め方">
        <ol className="grid gap-3">
          {guide.howto.map((step, index) => (
            <li key={index} className="grid grid-cols-[2rem_1fr] items-start gap-3">
              <span className="index-num grid size-8 place-items-center rounded-lg border border-line-strong text-meta font-semibold text-ink">
                {index + 1}
              </span>
              <span className="pt-1 text-desc leading-[1.8] text-ink">{step}</span>
            </li>
          ))}
        </ol>
      </Section>

      {affiliates.length > 0 && (
        <div className="mt-12">
          <AffiliateBox links={affiliates} />
        </div>
      )}

      {articles.length > 0 && (
        <Section id="news" title={`${guide.name}の最新ニュース`} note={`関連する発表 ${articles.length}本（新しい順・自動更新）`}>
          <Timeline articles={articles.slice(0, 12)} />
          {company.slug !== OTHER.slug && (
            <Link
              href={`/companies/${company.slug}`}
              className="mt-6 inline-flex items-center gap-1.5 text-note font-bold text-ink underline-offset-4 hover:underline"
            >
              {company.name}の最新ニュースをすべて見る →
            </Link>
          )}
        </Section>
      )}

      <Section id="faq" title="よくある質問">
        <div className="divide-y divide-line border-y border-line">
          {guide.faq.map((item) => (
            <details key={item.q} className="group py-4">
              <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-[15.5px] font-bold text-ink">
                <span>Q. {item.q}</span>
                <span aria-hidden className="mt-1 text-muted transition-transform group-open:rotate-45">＋</span>
              </summary>
              <p className="mt-3 text-desc leading-[1.9] text-ink-soft">A. {item.a}</p>
            </details>
          ))}
        </div>
      </Section>

      <p className="mt-12 rounded-2xl bg-surface px-5 py-4 text-note leading-relaxed text-muted">
        このガイドは、サイソクが公開した関連記事（各社の公式発表や解説動画をAIが解析したもの）をもとに編集部がまとめたものです。最新の仕様・料金は各社の公式発表をご確認ください。
      </p>
    </div>
  );
}

function Section({
  id,
  title,
  note,
  children,
}: {
  id: string;
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section aria-labelledby={`${id}-heading`} className="mt-14">
      <div className="mb-5 border-b border-ink pb-2">
        <h2 id={`${id}-heading`} className="t-h2">
          {title}
        </h2>
        {note && <p className="mt-0.5 text-meta text-muted">{note}</p>}
      </div>
      {children}
    </section>
  );
}
