import type { Metadata } from "next";
import Link from "next/link";
import Image from "next/image";
import { notFound } from "next/navigation";
import CompanyMark from "@/components/CompanyMark";
import Thumb from "@/components/Thumb";
import { coverPath, formatDate, getArticleSummary, type ArticleSummary } from "@/lib/articles";
import { getCompany, thumbSource } from "@/lib/companies";
import {
  getAllProducts,
  getProduct,
  hasBuyingInfo,
  productArticles,
  productHeading,
  productSeoTitle,
  type Product,
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
function describe(product: Product, count: number): string {
  const first = product.overview.text.split("。")[0];
  const tail = hasBuyingInfo(product)
    ? `料金・日本で使えるか・使い方・これまでの流れを${count}本の記事からまとめています。`
    : `できること・これまでの流れを${count}本の記事からまとめています。`;
  return `${first}。${tail}`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const product = getProduct(slug);
  if (!product) return {};
  const articles = productArticles(product);
  const cover = articles[0] ? coverPath(articles[0]) : null;
  const description = describe(product, articles.length);
  return {
    title: productSeoTitle(product),
    description,
    alternates: { canonical: `/products/${product.slug}` },
    openGraph: {
      type: "article",
      url: `/products/${product.slug}`,
      title: productHeading(product),
      description,
      modifiedTime: product.updatedAt,
      ...(cover ? { images: [{ url: cover, width: 1200, height: 630 }] } : {}),
    },
  };
}

const SOFTWARE = new Set(["AIモデル", "アプリ・サービス", "開発ツール"]);

/** 早見表の「料金」から円の価格を取り出す（ガジェットの Product 構造化データ用。取れなければ null） */
function yenPrice(product: Product): number | null {
  const value = product.facts.find((f) => f.label === "料金")?.value ?? "";
  const m = value.replace(/,/g, "").match(/(\d+)万(\d+)?円|(\d+)円/);
  if (!m) return null;
  return m[3] ? Number(m[3]) : Number(m[1]) * 10000 + Number(m[2] ?? 0);
}

export default async function ProductPage({ params }: Props) {
  const { slug } = await params;
  const product = getProduct(slug);
  if (!product) notFound();

  const articles = productArticles(product);
  // 色・企業ページのある企業だけ印を出す（それ以外は企業名だけ）
  const company = product.company === "other" ? undefined : getCompany(product.company);
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
  // 描画の前に番号を確定させる（見出しの順に並べる）
  const blocks = {
    overview: cite(product.overview.sources),
    facts: product.facts.map((f) => cite(f.sources)),
    capabilities: product.capabilities.map((i) => cite(i.sources)),
    pricing: product.pricing.map((i) => cite(i.sources)),
    japan: product.japan.map((i) => cite(i.sources)),
    howto: product.howto.map((i) => cite(i.sources)),
    differences: product.differences.map((i) => cite(i.sources)),
    faq: product.faq.map((i) => cite(i.sources)),
  };
  const cited = order.map((s) => getArticleSummary(s)!);
  const others = articles.filter((a) => !order.includes(a.slug)).slice(0, 8);
  const howtos = howtosForProduct(product.slug);
  const related = product.related.map((s) => getProduct(s)).filter((p): p is Product => Boolean(p));

  const joined = (items: Sourced[]) => items.map((i) => i.text).join("");
  const qa = [
    ...(product.pricing.length > 0 ? [{ q: `${product.name}の料金は？`, a: joined(product.pricing) }] : []),
    ...(product.japan.length > 0 ? [{ q: `${product.name}は日本で使える？`, a: joined(product.japan) }] : []),
    ...product.faq.map((f) => ({ q: f.q, a: f.a })),
  ];
  const price = product.category === "ガジェット" ? yenPrice(product) : null;

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: heading,
      description: describe(product, articles.length),
      inLanguage: "ja",
      datePublished: product.createdAt,
      dateModified: product.updatedAt,
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
    // ソフトウェアは SoftwareApplication。ガジェットは円の価格が記事でわかるときだけ Product（価格の無い Product はエラー扱い）
    ...(SOFTWARE.has(product.category)
      ? [
          {
            "@context": "https://schema.org",
            "@type": "SoftwareApplication",
            name: product.name,
            applicationCategory: product.category === "開発ツール" ? "DeveloperApplication" : "BusinessApplication",
            author: { "@type": "Organization", name: product.companyName },
            description: product.overview.text,
          },
        ]
      : price
        ? [
            {
              "@context": "https://schema.org",
              "@type": "Product",
              name: product.name,
              brand: { "@type": "Brand", name: product.companyName },
              description: product.overview.text,
              offers: { "@type": "Offer", price, priceCurrency: "JPY", url },
            },
          ]
        : []),
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

      <p className="mt-7 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta text-muted">
        {company ? (
          <Link href={`/companies/${company.slug}`} className="hover:opacity-80">
            <CompanyMark company={company} size={18} withName />
          </Link>
        ) : (
          <span className="font-bold text-ink">{product.companyName}</span>
        )}
        <span>{product.category}</span>
        <span className="index-num">
          最終更新 <time dateTime={product.updatedAt}>{formatDate(product.updatedAt)}</time>
        </span>
      </p>

      <h1 className="t-h1 mt-3">
        <Phrases text={heading} />
      </h1>

      {articles[0] && (
        <Link href={`/articles/${articles[0].slug}`} className="group mt-6 block overflow-hidden rounded-xl border border-line" aria-label={`最新の関連記事：${articles[0].title}`}>
          <span className="relative block aspect-[40/21] bg-surface-2">
            {articles[0].thumbnail ? (
              <Thumb src={articles[0].thumbnail} source={thumbSource(articles[0])} alt="" fill sizes="(max-width: 760px) 100vw, 760px" className="object-contain" />
            ) : (
              <Image src={coverPath(articles[0])!} alt="" fill sizes="(max-width: 760px) 100vw, 760px" className="object-contain" />
            )}
          </span>
          <span className="block px-4 py-3 text-note font-bold text-ink group-hover:underline">関連記事：{articles[0].title}</span>
        </Link>
      )}

      <p className="mt-6 text-[16px] leading-[2] text-ink">
        {product.overview.text}
        <Refs numbers={blocks.overview} />
      </p>

      {product.officialCheck && (
        <p className="mt-4 rounded-lg border border-line bg-surface px-4 py-3 text-note leading-[1.8] text-ink-soft">
          利用条件は公式情報を{product.officialCheck.date.replaceAll("-", "/")}に確認しました。
          <a href={product.officialCheck.url} className="ml-1 text-accent underline" target="_blank" rel="noopener noreferrer">{product.officialCheck.label}</a>
        </p>
      )}

      {product.facts.length > 0 && (
        <dl className="mt-7 border-t border-ink text-[16px]">
          {product.facts.map((fact, index) => (
            <div key={fact.label} className="grid grid-cols-[6.5rem_1fr] gap-3 border-b border-line py-3 sm:grid-cols-[8.5rem_1fr]">
              <dt className="font-bold text-ink-soft">{fact.label}</dt>
              <dd className="text-ink">
                {fact.value}
                <Refs numbers={blocks.facts[index]} />
              </dd>
            </div>
          ))}
        </dl>
      )}

      <List id="can" title="何ができる？" items={product.capabilities} refs={blocks.capabilities} />
      <Paragraphs id="price" title="料金は？" items={product.pricing} refs={blocks.pricing} />
      <Paragraphs id="japan" title="日本で使える？" items={product.japan} refs={blocks.japan} />
      <Paragraphs id="howto" title="どうやって使う？" items={product.howto} refs={blocks.howto} />
      <List
        id="diff"
        title={product.category === "AIモデル" ? "前のモデルと何が違う？" : "これまでと何が違う？"}
        items={product.differences}
        refs={blocks.differences}
      />

      {product.timeline.length > 0 && (
        <Section id="history" title="これまでの流れ">
          <ol className="border-l border-line-strong pl-5">
            {product.timeline.map((item, index) => {
              const sameDay = index > 0 && product.timeline[index - 1].date === item.date;
              return (
                <li key={item.slug} className={`relative ${sameDay ? "mt-2" : index > 0 ? "mt-5" : ""}`}>
                  {!sameDay && (
                    <>
                      <span aria-hidden className="absolute -left-[25px] top-[7px] size-[9px] rounded-full border-2 border-paper bg-ink" />
                      <span className="index-num block text-meta text-muted">{item.date.replaceAll("-", "/")}</span>
                    </>
                  )}
                  <Link
                    href={`/articles/${item.slug}`}
                    className="mt-0.5 block text-desc leading-[1.75] text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                  >
                    {item.event}
                  </Link>
                </li>
              );
            })}
          </ol>
        </Section>
      )}

      {product.faq.length > 0 && (
        <Section id="faq" title="よくある質問">
          <dl className="grid gap-6">
            {product.faq.map((item, index) => (
              <div key={item.q}>
                <dt className="text-[15.5px] font-bold leading-[1.7] text-ink">{item.q}</dt>
                <dd className="mt-1.5 text-desc leading-[1.95] text-ink-soft">
                  {item.a}
                  <Refs numbers={blocks.faq[index]} />
                </dd>
              </div>
            ))}
          </dl>
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

      {others.length > 0 && (
        <Section id="more" title="そのほかの関連記事">
          <ul className="grid gap-2.5 text-note">
            {others.map((article) => (
              <li key={article.slug}>
                <ArticleLine article={article} />
              </li>
            ))}
          </ul>
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
        このページは、サイソクが公開した{articles.length}本の関連記事をもとに情報を整理しています。
        各記事の出典と確認日をご覧ください。料金や提供条件は変わることがあるため、最新の情報は公式発表をご確認ください。
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

function Paragraphs({ id, title, items, refs }: { id: string; title: string; items: Sourced[]; refs: number[][] }) {
  if (items.length === 0) return null;
  return (
    <Section id={id} title={title}>
      <div className="grid gap-3">
        {items.map((item, index) => (
          <p key={index} className="text-desc leading-[1.95] text-ink">
            {item.text}
            <Refs numbers={refs[index]} />
          </p>
        ))}
      </div>
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
        {article.seoTitle || article.title}
      </Link>
    </span>
  );
}
