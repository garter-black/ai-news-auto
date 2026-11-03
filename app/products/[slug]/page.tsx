import { articleTitle } from "@/lib/headline";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import CompanyMark from "@/components/CompanyMark";
import ProductVisual from "@/components/ProductVisual";
import PriceTable from "@/components/PriceTable";
import { formatDate, getAllArticles, getArticleSummary, type ArticleSummary } from "@/lib/articles";
import { companyOf, getCompany } from "@/lib/companies";
import type { ModelRow, PlanRow } from "@/lib/pricing-ladder";
import {
  getAllProducts,
  getProduct,
  isListedProduct,
  productHeading,
  productProfile,
  productSeoTitle,
  type ProductProfile,
  type Sourced,
} from "@/lib/products";
import { Phrases } from "@/lib/highlight";
import { howtosForProduct } from "@/lib/howto";
import { siteConfig } from "@/site.config";
import { authorJsonLd, publisherJsonLd } from "@/lib/publisher";

type Props = { params: Promise<{ slug: string }> };

// まとめページはビルド時にすべて静的化する（データは scripts/products.py が更新する）
export const dynamicParams = false;

export function generateStaticParams() {
  return getAllProducts().map((product) => ({ slug: product.slug }));
}

/** 検索結果の説明文（120字前後） */
function describe(profile: ProductProfile): string {
  const first = profile.overview.split("。")[0];
  const parts = [profile.startingPrice ? `料金は${profile.startingPrice}` : "", profile.japan ? profile.japan.label : ""].filter(Boolean);
  return `${first}。${parts.length ? `${parts.join("・")}。` : ""}公式の料金・提供状況と発表の履歴を確認日つきで整理しています。`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const product = getProduct(slug);
  if (!product) return {};
  const profile = productProfile(product);
  const description = describe(profile);
  return {
    title: productSeoTitle(product),
    description,
    alternates: { canonical: `/products/${product.slug}` },
    // 中身がそろわない製品は検索に出さない（一覧にも出さない。データがそろえば自動で外れる）
    ...(profile.listing.listed ? {} : { robots: { index: false, follow: true } }),
    // 記事の写真は使わない（関係のない写真が出ていた。2026-11-03）。共有画像はサイトの既定
    openGraph: {
      siteName: siteConfig.siteName,
      locale: siteConfig.locale,
      type: "article",
      url: `/products/${product.slug}`,
      title: productHeading(product),
      description,
      modifiedTime: profile.updatedAt,
    },
  };
}

const SOFTWARE = new Set(["AIモデル", "アプリ・サービス", "開発ツール"]);

/** 料金のよくある質問の答え（公式の料金の表から作る） */
function priceAnswer(profile: ProductProfile): string {
  const table = profile.pricingTable;
  if (!table) return "";
  const rows =
    table.kind === "api"
      ? (table.rows as ModelRow[]).map((r) => `${r.name}${r.context ? `（${r.context}）` : ""}は入力100万トークンあたり${r.inputText}、出力${r.outputText}`)
      : (table.rows as PlanRow[]).map((r) => `${r.name}は${r.priceTexts.join("、")}`);
  return `公式の料金ページ（確認日 ${table.checkedAt}）では、${rows.join("。")}です。`;
}

export default async function ProductPage({ params }: Props) {
  const { slug } = await params;
  const product = getProduct(slug);
  if (!product) notFound();

  const profile = productProfile(product);
  const { articles } = profile;
  // 企業ページ（/companies/<slug>）は AI の記事がある企業だけ開ける（app/companies/[company]/view.tsx）。無ければ名前だけ出す
  const companyCandidate = product.company === "other" ? undefined : getCompany(product.company);
  const company =
    companyCandidate && getAllArticles().some((a) => a.genreSlug === "ai" && companyOf(a).slug === companyCandidate.slug) ? companyCandidate : undefined;
  const heading = productHeading(product);
  const url = `${siteConfig.url}/products/${product.slug}`;

  // 出典の番号（ページに出てくる順）
  const order: string[] = [];
  const cite = (sources: string[]) =>
    sources
      .filter((s) => getArticleSummary(s))
      .map((s) => {
        if (!order.includes(s)) order.push(s);
        return order.indexOf(s) + 1;
      })
      .sort((a, b) => a - b);
  const blocks = {
    overview: cite(profile.overviewSources),
    facts: profile.facts.map((f) => cite(f.sources)),
    features: profile.features.map((i) => cite(i.sources)),
    changes: profile.changes.map((i) => cite(i.sources)),
    howto: profile.howto.map((i) => cite(i.sources)),
    faq: profile.faq.map((i) => cite(i.sources)),
  };
  const cited = order.map((s) => getArticleSummary(s)!);
  const latest = profile.latest;
  const howtos = howtosForProduct(product.slug);
  const related = product.related.filter((s) => isListedProduct(s)).map((s) => getProduct(s)!);

  const qa = [
    ...(profile.pricingTable ? [{ q: `${product.name}の料金はいくらですか？`, a: priceAnswer(profile), sources: [] as string[] }] : []),
    ...(profile.japan
      ? [{ q: `${product.name}は日本で使えますか？`, a: `${profile.japan.label}です。${profile.japan.covers}について、${profile.japan.note}（確認日 ${profile.japan.checkedAt}）`, sources: [] as string[] }]
      : []),
    ...profile.faq,
  ];
  const faqRefs = [...qa.slice(0, qa.length - profile.faq.length).map(() => [] as number[]), ...blocks.faq];

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: heading,
      description: describe(profile),
      inLanguage: "ja",
      datePublished: product.createdAt,
      dateModified: profile.updatedAt,
      mainEntityOfPage: url,
      author: authorJsonLd,
      publisher: publisherJsonLd,
      publishingPrinciples: `${siteConfig.url}/editorial-policy`,
      about: { "@type": "Thing", name: product.name },
      citation: cited.map((a) => ({ "@type": "NewsArticle", headline: a.title, url: `${siteConfig.url}/articles/${a.slug}` })),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: "AIサービス図鑑", item: `${siteConfig.url}/products` },
        { "@type": "ListItem", position: 3, name: product.name },
      ],
    },
    ...(qa.length > 0
      ? [
          {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            mainEntity: qa.map(({ q, a }) => ({ "@type": "Question", name: q, acceptedAnswer: { "@type": "Answer", text: a } })),
          },
        ]
      : []),
    ...(SOFTWARE.has(product.category)
      ? [
          {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            name: product.name,
            applicationCategory: product.category === "開発ツール" ? "DeveloperApplication" : "BusinessApplication",
            author: { "@type": "Organization", name: product.companyName },
            description: profile.overview,
          },
        ]
      : []),
  ];

  const summary: { label: string; value: React.ReactNode; refs?: number[] }[] = [
    { label: "提供元", value: product.companyName },
    { label: "分類", value: product.category },
    ...(profile.startingPrice
      ? [{ label: "料金", value: <>{profile.startingPrice}{profile.startingPriceNote ? <span className="ml-1.5 text-ink-soft">（{profile.startingPriceNote}）</span> : null}</> }]
      : []),
    ...(profile.japan ? [{ label: "日本での提供", value: profile.japan.label }] : []),
    ...(profile.audience ? [{ label: "主な対象", value: profile.audience }] : []),
    ...profile.facts.filter((f) => f.label !== "向いている人").map((f) => ({ label: f.label, value: f.value, refs: blocks.facts[profile.facts.indexOf(f)] })),
  ];

  return (
    <div className="mx-auto max-w-[760px] px-4 py-8 sm:px-8 sm:py-12">
      <script
        type="application/ld+json"
        suppressHydrationWarning
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav aria-label="パンくず" className="flex flex-wrap items-center gap-1.5 text-meta text-muted">
        <Link href="/" className="hover:text-accent">ホーム</Link>
        <span aria-hidden>/</span>
        <Link href="/products" className="hover:text-accent">AIサービス図鑑</Link>
        <span aria-hidden>/</span>
        <span>{product.name}</span>
      </nav>

      <div className="mt-7 flex items-start gap-4">
        <ProductVisual company={product.company} image={profile.source?.image} size={64} />
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted">
            {company ? (
              <Link href={`/companies/${company.slug}`} className="hover:opacity-80">
                <CompanyMark company={company} size={18} withName />
              </Link>
            ) : (
              <span className="font-bold text-ink">{product.companyName}</span>
            )}
            <span>{product.category}</span>
            <span className="index-num">
              最終更新 <time dateTime={profile.updatedAt}>{profile.updatedAt.slice(0, 10).replaceAll("-", "/")}</time>
            </span>
          </p>
          <h1 className="t-h1 mt-2">
            <Phrases text={heading} />
          </h1>
        </div>
      </div>
      {profile.source?.image && (
        <p className="mt-2 text-meta text-muted">
          {profile.source.image.credit}
          <a href={profile.source.image.sourceUrl} className="ml-1 underline underline-offset-2 hover:text-accent" target="_blank" rel="noopener noreferrer">
            出典
          </a>
        </p>
      )}

      <p className="mt-6 text-[16px] leading-[2] text-ink">
        {profile.overview}
        <Refs numbers={blocks.overview} />
      </p>

      {product.officialCheck && (
        <p className="mt-4 rounded-lg border border-line bg-surface px-4 py-3 text-note leading-[1.8] text-ink-soft">
          利用条件は公式情報を{product.officialCheck.date.replaceAll("-", "/")}に確認しました。
          <a href={product.officialCheck.url} className="ml-1 text-accent underline" target="_blank" rel="noopener noreferrer">{product.officialCheck.label}</a>
        </p>
      )}

      <dl className="mt-7 border-t border-ink text-[16px]">
        {summary.map((row) => (
          <div key={row.label} className="grid grid-cols-[6.5rem_1fr] gap-3 border-b border-line py-3 sm:grid-cols-[8.5rem_1fr]">
            <dt className="font-bold text-ink-soft">{row.label}</dt>
            <dd className="text-ink">
              {row.value}
              {row.refs && <Refs numbers={row.refs} />}
            </dd>
          </div>
        ))}
      </dl>

      {profile.pricingTable && (
        <Section id="price" title="料金プラン">
          <PriceTable table={profile.pricingTable} />
          {profile.source?.why && <p className="mt-3 text-note leading-[1.8] text-ink-soft">この製品の料金として載せた根拠：{profile.source.why}。</p>}
        </Section>
      )}

      {profile.japan && (
        <Section id="japan" title="日本での提供状況">
          <p className="text-desc leading-[1.95] text-ink">
            <span className="font-bold">{profile.japan.label}</span>。{profile.japan.covers}について、{profile.japan.note}
          </p>
          <p className="mt-2 text-note leading-[1.8] text-ink-soft">
            出典：
            <a href={profile.japan.url} className="text-accent underline underline-offset-2" target="_blank" rel="noopener noreferrer">
              {profile.japan.url.replace(/^https?:\/\//, "").split("/")[0]}
            </a>
            （確認日 {profile.japan.checkedAt}）。新しい機能は国・地域やプランによって順番に提供されることがあります。
          </p>
        </Section>
      )}

      <List id="features" title="主な機能・仕様" items={profile.features} refs={blocks.features} />
      <List id="changes" title={product.category === "AIモデル" ? "前のモデルからの変更点" : "最近の変更点"} items={profile.changes} refs={blocks.changes} />
      <List id="howto" title="利用の始め方" items={profile.howto} refs={blocks.howto} />

      {profile.rivals && (
        <Section id="rivals" title="同じ価格帯のサービスとの比較">
          <PriceTable table={profile.rivals} />
        </Section>
      )}

      {profile.timeline.length > 0 && (
        <Section id="history" title="発表・更新の履歴">
          <ol className="border-l border-line-strong pl-5">
            {profile.timeline.map((item, index) => {
              const linked = Boolean(getArticleSummary(item.slug));
              const sameDay = index > 0 && profile.timeline[index - 1].date === item.date;
              return (
                <li key={item.slug} className={`relative ${sameDay ? "mt-2" : index > 0 ? "mt-5" : ""}`}>
                  {!sameDay && (
                    <>
                      <span aria-hidden className="absolute -left-[25px] top-[7px] size-[9px] rounded-full border-2 border-paper bg-ink" />
                      <span className="index-num block text-meta text-muted">{item.date.replaceAll("-", "/")}</span>
                    </>
                  )}
                  {linked ? (
                    <Link
                      href={`/articles/${item.slug}`}
                      className="mt-0.5 block text-desc leading-[1.75] text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                    >
                      {item.event}
                    </Link>
                  ) : (
                    <span className="mt-0.5 block text-desc leading-[1.75] text-ink">{item.event}</span>
                  )}
                </li>
              );
            })}
          </ol>
        </Section>
      )}

      {qa.length > 0 && (
        <Section id="faq" title="よくある質問">
          <dl className="grid gap-6">
            {qa.map((item, index) => (
              <div key={item.q}>
                <dt className="text-[15.5px] font-bold leading-[1.7] text-ink">{item.q}</dt>
                <dd className="mt-1.5 text-desc leading-[1.95] text-ink-soft">
                  {item.a}
                  <Refs numbers={faqRefs[index]} />
                </dd>
              </div>
            ))}
          </dl>
        </Section>
      )}

      {latest.length > 0 && (
        <Section id="latest" title="最新の関連記事">
          <ul className="grid gap-2.5 text-note">
            {latest.map((article) => (
              <li key={article.slug}>
                <ArticleLine article={article} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {cited.length > 0 && (
        <Section id="refs" title="参考にした記事">
          <ol className="grid gap-2.5 text-note">
            {cited.map((article, index) => (
              <li key={article.slug} id={`ref-${index + 1}`} className="grid grid-cols-[2rem_1fr] scroll-mt-24">
                <span className="index-num text-note leading-[1.9] text-muted">[{index + 1}]</span>
                <ArticleLine article={article} />
              </li>
            ))}
          </ol>
        </Section>
      )}

      {howtos.length > 0 && (
        <Section id="howto-guides" title="使い方ガイド">
          <ul className="grid gap-2.5">
            {howtos.map((guide) => (
              <li key={guide.slug} className="text-[14.5px] leading-snug">
                <Link
                  href={`/guides/${guide.slug}`}
                  className="font-bold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                >
                  {guide.title}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {related.length > 0 && (
        <Section id="related" title="関連する製品">
          <ul className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
            {related.map((other) => (
              <li key={other.slug} className="text-[14.5px]">
                <Link
                  href={`/products/${other.slug}`}
                  className="font-bold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                >
                  {other.name}
                </Link>
                <span className="ml-2 text-meta text-muted">{other.category}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <p className="mt-14 border-t border-line pt-5 text-note leading-[1.9] text-muted">
        料金は各社の公式料金ページ、日本での提供状況は公式の対応国・地域の案内で確認し、それぞれに確認日を付けています。機能と発表の履歴は、サイソクが公開した{articles.length}本の関連記事から整理しました。料金や提供条件は変わることがあるため、契約の前に公式ページもご確認ください。
        <Link href="/editorial-policy" className="ml-1 text-accent underline">
          編集方針
        </Link>
      </p>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={`${id}-heading`} className="mt-12 sm:mt-14">
      <h2 id={`${id}-heading`} className="t-h2 mb-4 border-b border-line pb-2">
        {title}
      </h2>
      {children}
    </section>
  );
}

function List({ id, title, items, refs }: { id: string; title: string; items: Sourced[]; refs: number[][] }) {
  if (items.length === 0) return null;
  return (
    <Section id={id} title={title}>
      <ul className="grid gap-3">
        {items.map((item, index) => (
          <li key={index} className="grid grid-cols-[1rem_1fr] text-desc leading-[1.9] text-ink">
            <span aria-hidden className="mt-[0.8em] size-[5px] rounded-full bg-muted" />
            <span>
              {item.text}
              <Refs numbers={refs[index]} />
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

/** 出典の番号（ページ下の「参考にした記事」へ） */
function Refs({ numbers }: { numbers: number[] }) {
  if (!numbers || numbers.length === 0) return null;
  return (
    <sup className="index-num ml-0.5 whitespace-nowrap text-meta">
      {numbers.map((n) => (
        <a key={n} href={`#ref-${n}`} className="ml-0.5 text-muted hover:text-accent" aria-label={`出典${n}`}>
          [{n}]
        </a>
      ))}
    </sup>
  );
}

function ArticleLine({ article }: { article: ArticleSummary }) {
  return (
    <span className="leading-[1.8]">
      <span className="index-num mr-2 text-meta text-muted">{formatDate(article.publishedAt)}</span>
      <Link
        href={`/articles/${article.slug}`}
        className="text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
      >
        {articleTitle(article)}
      </Link>
    </span>
  );
}
