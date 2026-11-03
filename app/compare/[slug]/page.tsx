import { COMPARE_PUBLIC } from "@/lib/buying-guides";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import ArticleCard from "@/components/ArticleCard";
import { Pixels, ProductImage, checkedDate, priceText } from "@/components/rail/parts";
import { formatDate } from "@/lib/articles";
import {
  getAllBuyingGuides,
  getBuyingGuide,
  guideRelatedArticles,
  isRichGuide,
  resolveProducts,
  type ResolvedProduct,
} from "@/lib/buying-guides";
import VariantA from "@/components/compare/VariantA";
import { buildGuideView } from "@/components/compare/view";
import { siteConfig } from "@/site.config";
import { publisherJsonLd } from "@/lib/publisher";
import { Phrases } from "@/lib/highlight";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllBuyingGuides().map((guide) => ({ slug: guide.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const guide = getBuyingGuide(slug);
  if (!guide) return {};
  const url = `/compare/${guide.slug}`;
  return {
    title: guide.title,
    description: guide.description,
    keywords: guide.targetQueries,
    alternates: { canonical: url },
    ...(COMPARE_PUBLIC ? {} : { robots: { index: false, follow: true } }),
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale,
      type: "article",
      url,
      title: guide.title,
      description: guide.description,
      publishedTime: guide.publishedAt,
      modifiedTime: guide.updatedAt,
    },
  };
}

/** 価格の出典（どこの・いつの価格か） */
function priceSourceText(product: ResolvedProduct): string {
  const source = product.priceSource;
  if (!product.price || !source) return "価格は公式サイトでご確認ください";
  const when = `${checkedDate(source.checkedAt)}時点`;
  return source.type === "official" ? `公式サイトの表示・${when}` : `公式ストアの価格・${when}`;
}

export default async function ComparePage({ params }: Props) {
  const { slug } = await params;
  const guide = getBuyingGuide(slug);
  if (!guide) notFound();

  // 作り直した新形式のガイド（提携外の商品も並べる）は新しいデザインで出す。
  // 採用したデザイン（A 報道テロップ型）。比較用の B・C 案は 2026-10-28 に削除
  if (isRichGuide(guide)) return <VariantA view={buildGuideView(guide)} />;

  const products = resolveProducts(guide);
  const byKey = new Map(products.map((p) => [p.partner, p]));
  const picks = guide.quickPick.flatMap((pick) => {
    const product = byKey.get(pick.partner);
    return product ? [{ ...pick, product }] : [];
  });
  const related = guideRelatedArticles(guide);
  const pixels = [...new Set(products.flatMap((p) => (p.pixel ? [p.pixel] : [])))];
  const asOf = checkedDate(guide.priceCheckedAt);
  const url = `${siteConfig.url}/compare/${guide.slug}`;

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: guide.title,
      description: guide.description,
      inLanguage: "ja",
      datePublished: guide.publishedAt,
      dateModified: guide.updatedAt,
      mainEntityOfPage: url,
      author: { "@type": "Organization", name: siteConfig.author, url: `${siteConfig.url}/about` },
      publisher: publisherJsonLd,
      publishingPrinciples: `${siteConfig.url}/editorial-policy`,
      keywords: guide.targetQueries.join(", "),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: "比較ガイド", item: `${siteConfig.url}/compare` },
        { "@type": "ListItem", position: 3, name: guide.title, item: url },
      ],
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
    <div className="relative mx-auto max-w-[920px] px-5 py-8 sm:px-8 sm:py-12">
      <script
        type="application/ld+json"
        suppressHydrationWarning
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav aria-label="パンくず" className="flex flex-wrap items-center gap-1.5 text-meta text-muted">
        <Link href="/" className="hover:text-accent">ホーム</Link>
        <span aria-hidden>/</span>
        <Link href="/compare" className="hover:text-accent">比較ガイド</Link>
      </nav>

      {/* 広告の明示（A8・景品表示法）と、情報の時点 */}
      <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta">
        <span className="rounded border border-ink px-1.5 text-badge font-bold tracking-wider text-ink">PR</span>
        <span className="rounded-sm bg-ink px-2.5 py-0.5 text-badge font-bold tracking-wider text-paper">比較ガイド</span>
        <span className="index-num text-muted">
          最終更新 <time dateTime={guide.updatedAt} className="font-semibold text-ink">{formatDate(guide.updatedAt)}</time>
        </span>
      </div>

      <h1 className="t-h1 mt-3"><Phrases text={guide.title} /></h1>

      <p className="mt-5 rounded-xl bg-surface px-4 py-3 text-note leading-[1.8] text-ink-soft">
        <span className="font-bold text-ink">この記事は{asOf}時点の公式情報をもとに作成しています。</span>
        編集部が実際に購入・使用して評価したものではありません。本ページはプロモーション（アフィリエイト広告）を含み、掲載している商品はすべて当サイトが提携している広告主のもので、リンク経由の申し込みで当サイトに紹介料が入ることがあります。
      </p>

      <p className="mt-6 text-desc leading-[1.95] text-ink-soft">{guide.intro}</p>

      {guide.slug === "vps-dify-n8n" && (
        <aside aria-label="Difyとn8nの導入条件" className="mt-6 rounded-xl border border-line bg-surface p-5">
          <h2 className="t-h2">料金を比べる前に、動かすアプリの要件を確認</h2>
          <p className="mt-3 text-desc leading-[1.9] text-ink-soft">
            2026年10月30日に確認したDify公式の最低条件はCPU 2コア・メモリ4GiBです。XServerのDifyアプリイメージも4GB以上のプランが対象です。下のVPS全体の開始価格が、そのままDify対応プランの価格とは限りません。n8nは処理するデータ量や同時実行などで必要メモリが変わります。
          </p>
          <p className="mt-3 text-note leading-[1.8] text-ink-soft">
            根拠：<a href="https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose" target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">Dify公式導入要件</a>
            、<a href="https://vps.xserver.ne.jp/dify.php" target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">XServerのDify対応条件</a>
            、<a href="https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/fix-memory-issues" target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">n8n公式のメモリ説明</a>。
          </p>
          <ul className="mt-4 grid gap-2 text-desc leading-[1.8]">
            <li><Link href="/guides/dify-free-api-cost" className="font-bold underline underline-offset-4">Difyを試す前に、無料枠とAPIの請求先を確認する</Link></li>
            <li><Link href="/guides/dify-1-18-startup-check" className="font-bold underline underline-offset-4">Dify 1.18.0が起動しないときの確認順を読む</Link></li>
          </ul>
        </aside>
      )}

      {/* 迷ったらこれ */}
      {picks.length > 0 && (
        <section aria-labelledby="pick-heading" className="mt-8 overflow-hidden rounded-2xl border border-ink">
          <div className="flex items-center gap-2 bg-ink px-5 py-3 text-paper">
            <span className="rounded border border-current px-1.5 text-badge font-bold tracking-wider opacity-80">PR</span>
            <h2 id="pick-heading" className="t-h3 leading-snug">迷ったらこれ</h2>
          </div>
          <ol className={`grid divide-y divide-line sm:divide-x sm:divide-y-0 ${picks.length >= 3 ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}>
            {picks.map(({ reason, product }) => (
              <li key={product.key} className="flex">
                <a
                  href={product.item.url}
                  target="_blank"
                  rel="sponsored nofollow noopener noreferrer"
                  className="group flex w-full flex-col gap-2.5 p-4"
                >
                  <span className="text-meta font-bold leading-snug text-live">{reason}</span>
                  <ProductImage item={product.item} className="aspect-[16/10] rounded-xl border border-line" pad="p-3" eager />
                  <span className="text-[14.5px] font-bold leading-snug text-ink group-hover:underline">{product.item.name}</span>
                  <span className="index-num text-[15px] font-bold text-ink">{priceText(product.item)}</span>
                  <span className="mt-auto flex h-11 items-center justify-center gap-1.5 rounded-xl bg-live text-note font-bold text-live-ink">
                    公式サイトで見る
                  </span>
                </a>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* 比較表（スマホは横にスクロール） */}
      <Section id="table" title="比較表" note={`価格は${asOf}時点。最新の料金・キャンペーンは各公式サイトでご確認ください`}>
        <div className="relative -mx-5 overflow-x-auto px-5 sm:mx-0 sm:px-0">
          <table className="w-full min-w-[720px] border-collapse text-left text-note leading-[1.6]">
            <thead>
              <tr className="border-b-2 border-ink">
                <th scope="col" className="sticky left-0 z-10 bg-paper py-2.5 pr-3 font-bold text-ink">商品</th>
                <th scope="col" className="px-3 py-2.5 font-bold text-ink">価格</th>
                {guide.columns.map((column) => (
                  <th key={column} scope="col" className="px-3 py-2.5 font-bold text-ink">{column}</th>
                ))}
                <th scope="col" className="px-3 py-2.5 font-bold text-ink">向いている人</th>
                <th scope="col" className="py-2.5 pl-3"><span className="sr-only">リンク</span></th>
              </tr>
            </thead>
            <tbody>
              {products.map((product) => (
                <tr key={product.key} className="border-b border-line align-top">
                  <th scope="row" className="sticky left-0 z-10 w-[150px] bg-paper py-3 pr-3 font-bold text-ink">
                    <a href={`#p-${product.key}`} className="hover:underline">{product.item.name}</a>
                  </th>
                  <td className="index-num px-3 py-3 font-bold text-ink">
                    {priceText(product.item)}
                    {product.priceNote && <span className="mt-0.5 block text-meta font-normal text-muted">{product.priceNote}</span>}
                  </td>
                  {guide.columns.map((column, i) => (
                    <td key={column} className="px-3 py-3 text-ink-soft">{product.specs?.[i] ?? "—"}</td>
                  ))}
                  <td className="px-3 py-3 text-ink-soft">{product.bestFor}</td>
                  <td className="py-3 pl-3">
                    <a
                      href={product.item.url}
                      target="_blank"
                      rel="sponsored nofollow noopener noreferrer"
                      className="inline-block whitespace-nowrap rounded-sm bg-ink px-3 py-1.5 text-badge font-bold text-paper hover:opacity-85"
                    >
                      公式
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section id="criteria" title="選び方・比べたポイント">
        <ol className="grid gap-3">
          {guide.criteria.map((point, index) => (
            <li key={index} className="grid grid-cols-[2rem_1fr] items-start gap-3">
              <span className="index-num grid size-8 place-items-center rounded-lg border border-line-strong text-meta font-semibold text-ink">
                {index + 1}
              </span>
              <span className="pt-1 text-desc leading-[1.8] text-ink">{point}</span>
            </li>
          ))}
        </ol>
      </Section>

      <Section id="products" title="商品ごとの特徴">
        <div className="grid gap-6">
          {products.map((product) => (
            <article key={product.key} id={`p-${product.key}`} className="scroll-mt-24 rounded-2xl border border-line p-5 sm:p-6">
              <div className="grid gap-5 sm:grid-cols-[180px_minmax(0,1fr)]">
                <ProductImage item={product.item} className="aspect-square rounded-xl border border-line" pad="p-3" />
                <div className="min-w-0">
                  {product.item.label && <p className="text-meta font-bold text-live">{product.item.label}</p>}
                  <h3 className="mt-0.5 text-[19px] font-bold leading-snug text-ink">{product.item.name}</h3>
                  <p className="mt-2 text-desc leading-[1.85] text-ink-soft">{product.summary}</p>
                  <p className="mt-3 flex flex-wrap items-baseline gap-x-2">
                    <span className="index-num text-[20px] font-bold text-ink">{priceText(product.item)}</span>
                    {product.priceNote && <span className="text-meta text-muted">{product.priceNote}</span>}
                  </p>
                  <p className="text-meta text-muted">{priceSourceText(product)}</p>
                </div>
              </div>
              <div className="mt-5 grid gap-4 sm:grid-cols-2">
                <div>
                  <h4 className="text-meta font-bold text-ink">公式情報から見た強み</h4>
                  <ul className="mt-2 grid gap-1.5 text-desc leading-[1.75] text-ink-soft">
                    {product.strengths.map((s) => (
                      <li key={s} className="grid grid-cols-[1rem_1fr] gap-1"><span aria-hidden className="text-go">○</span>{s}</li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h4 className="text-meta font-bold text-ink">選ぶ前に確認したい点</h4>
                  <ul className="mt-2 grid gap-1.5 text-desc leading-[1.75] text-ink-soft">
                    {product.limits.map((s) => (
                      <li key={s} className="grid grid-cols-[1rem_1fr] gap-1"><span aria-hidden className="text-muted">△</span>{s}</li>
                    ))}
                  </ul>
                </div>
              </div>
              <p className="mt-4 rounded-lg bg-surface px-3 py-2 text-desc text-ink-soft">
                <span className="font-bold text-ink">向いている人：</span>{product.bestFor}
              </p>
              <a
                href={product.item.url}
                target="_blank"
                rel="sponsored nofollow noopener noreferrer"
                className="mt-4 flex h-12 items-center justify-center gap-1.5 rounded-xl bg-live text-note font-bold text-live-ink transition-transform hover:-translate-y-px motion-reduce:transition-none"
              >
                {product.item.name}の公式サイトを見る
              </a>
            </article>
          ))}
        </div>
      </Section>

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

      {related.length > 0 && (
        <Section id="news" title="関連ニュース" note="サイソクが報じた関連の発表（新しい順）">
          <div className="grid gap-5 sm:grid-cols-2">
            {related.map((article) => (
              <ArticleCard key={article.slug} article={article} />
            ))}
          </div>
        </Section>
      )}

      <p className="mt-12 rounded-2xl bg-surface px-5 py-4 text-note leading-relaxed text-muted">
        このガイドは、各社の公式サイトに{asOf}時点で掲載されていた情報と価格、サイソクの関連記事をもとに、AIで下書きし事実確認をしたうえで公開しています。価格・仕様・キャンペーンは変わることがあります。申し込み前に必ず各公式サイトで最新情報をご確認ください。
        制作方法は<Link href="/editorial-policy" className="mx-1 text-accent underline">編集方針</Link>、
        誤りのご指摘は<Link href="/contact" className="mx-1 text-accent underline">お問い合わせ</Link>から受け付けています。
      </p>

      <Pixels pixels={pixels} />
    </div>
  );
}

function Section({ id, title, note, children }: { id: string; title: string; note?: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={`${id}-heading`} className="mt-14">
      <div className="mb-5 border-b border-ink pb-2">
        <h2 id={`${id}-heading`} className="t-h2">{title}</h2>
        {note && <p className="mt-0.5 text-meta text-muted">{note}</p>}
      </div>
      {children}
    </section>
  );
}
