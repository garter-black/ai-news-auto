import { isIndexable } from "@/lib/indexing";
import type { Metadata } from "next";
import Thumb from "@/components/Thumb";
import ImageCredit from "@/components/ImageCredit";
import Link from "next/link";
import { notFound } from "next/navigation";
import AdSlot from "@/components/AdSlot";
import MobilePrBar from "@/components/rail/MobilePrBar";
import RailRanking from "@/components/rail/RailRanking";
import ArticleCard from "@/components/ArticleCard";
import CompanyMark from "@/components/CompanyMark";
import CtaBanner from "@/components/CtaBanner";
import RelTime from "@/components/RelTime";
import NextRead from "@/components/NextRead";
import NewsletterSignup from "@/components/NewsletterSignup";
import PushOptIn from "@/components/PushOptIn";
import InlineRead from "@/components/InlineRead";
import ReadingMode from "@/components/ReadingMode";
import VerdictCard from "@/components/VerdictCard";
import AssetLinks from "@/components/assets/AssetLinks";
import SourceRecord from "@/components/SourceRecord";
import { companyOf, thumbSource } from "@/lib/companies";
import { guideForArticle } from "@/lib/guides";
import { howtoForArticle } from "@/lib/howto";
import { getProduct, productForArticle } from "@/lib/products";
import { buyingGuideForTopic, guideShortName } from "@/lib/buying-guides";
import { chooseGuideForArticle } from "@/lib/choose";
import type { ChooseGuide } from "@/lib/choose-data";
import { STATUS_LABEL, freeLabel, japanLabel, verdictOf } from "@/lib/verdict";
import { offerForArticle } from "@/lib/offers";
import { shopLinkForArticle } from "@/lib/amazon";
import ShopLinks from "@/components/ShopLinks";
import Morph from "@/components/Morph";
import RoundupSources from "@/components/RoundupSources";
import Sidebar from "@/components/Sidebar";
import {
  coverPath,
  formatDate,
  formatDateTime,
  getAllArticles,
  getArticle,
  getArticleSummary,
  getNextReads,
  getRelatedArticles,
  isVideo,
  isWeb,
  lastModifiedOf,
  PRERENDER_LIMIT,
  sourceHref,
  sourceLabel,
  speedLabel,
  tagHref,
} from "@/lib/articles";
import { Highlight } from "@/lib/highlight";
import { isOwnImage } from "@/lib/thumb-url";
import {
  extractHeadings,
  renderMarkdown,
  splitBeforeHeading,
} from "@/lib/markdown";
import { siteConfig } from "@/site.config";
import { authorJsonLd, publisherJsonLd } from "@/lib/publisher";

type Props = { params: Promise<{ slug: string }> };

// 新しい記事だけビルド時に静的化し、残りは初回アクセス時に生成してキャッシュする（ISR）。
// 記事が無い slug は notFound() で 404
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return getAllArticles()
    .slice(0, PRERENDER_LIMIT)
    .map((article) => ({ slug: article.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) return {};

  const url = `/articles/${article.slug}`;
  const cover = coverPath(article);
  return {
    // 検索結果では全角30字前後で切れるため、短い題名を優先する。
    // 記事内の見出し・OGP（SNSカード）は情報量の多い本来の title のまま
    title: article.seoTitle || article.title,
    description: article.description,
    keywords: article.keywords,
    alternates: { canonical: url },
    // 薄くなりやすい記事はサイトでは読めるが、検索には出さない（lib/indexing.ts）
    ...(isIndexable({ ...article, duplicate: getArticleSummary(article.slug)?.duplicate }) ? {} : { robots: { index: false, follow: true } }),
    openGraph: {
      type: "article",
      url,
      title: article.title,
      description: article.description,
      publishedTime: article.generatedAt || article.publishedAt,
      modifiedTime: lastModifiedOf(article),
      tags: article.tags,
      // SNSのカードは日本語見出し入りの独自カバー。無い記事は元動画（元記事）の画像
      images: cover
        ? [{ url: cover, width: 1200, height: 630, alt: article.seoTitle || article.title }]
        : article.thumbnail
          ? [{ url: article.thumbnail, width: 1280, height: 720 }]
          : undefined,
    },
    twitter: {
      card: "summary_large_image",
      title: article.title,
      description: article.description,
      images: cover ? [cover] : article.thumbnail ? [article.thumbnail] : undefined,
    },
  };
}

export default async function ArticlePage({ params }: Props) {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) notFound();

  const isRoundup = article.type === "roundup";
  // 公式ブログ・プレスリリースから作った記事（動画の埋め込み・タイムテーブルを出さない）
  const web = isWeb(article);
  const video = isVideo(article);
  // フリー素材・AI 生成のイメージ写真（scripts/images.py）
  const stockPhoto = article.imageCredit?.kind === "stock" || article.imageCredit?.kind === "ai";
  const sourceUrl = sourceHref(article);
  const sourceName = sourceLabel(article);
  const affiliates = article.affiliates ?? [];
  const company = companyOf(article);
  const product = productForArticle(article.slug);
  // まとめページへ移した製品ガイドは案内しない（/guides/<slug> は /products/<slug> へ転送）
  const guideMatch = product ? undefined : guideForArticle(article);
  const guide = guideMatch && !getProduct(guideMatch.slug) ? guideMatch : undefined;
  // 題材の使い方ガイド（「料金・無料でどこまで」を知りたい読者の受け皿）
  const howto = guide ? undefined : howtoForArticle(article);
  const verdict = verdictOf(article);
  // 全AI記事に、文脈に合った「試すなら」枠を1つ出す。無い場合（AI以外など）は従来の関連ツール枠
  // 右側と本文中で同じ商品を出さないよう、候補を多めに取って2つに分ける。
  // 各枠は候補の中から、この端末でまだ見ていない商品を先に3つ出す（RankingBody）
  const offerAll = offerForArticle(article, 12);
  const pool = offerAll?.items ?? [];
  const offer = offerAll && { ...offerAll, items: [...pool.slice(0, 3), ...pool.slice(6, 9)] };
  const inlineItems = [...pool.slice(3, 6), ...pool.slice(9, 12)];
  const inlineOffer =
    offerAll && inlineItems.length >= 2 ? { ...offerAll, eyebrow: "あわせてチェック", items: inlineItems } : undefined;
  // 商品枠の話題に比較ガイドがあれば、商品枠のすぐ下に1行だけ案内する
  const buyingGuide = buyingGuideForTopic(offerAll?.topic);
  // 製品を扱う記事だけ、本文中の商品枠のそばに「Amazon / 楽天で見る」を1行（右側には出さない）
  const shopLink = shopLinkForArticle(article);
  // 回線・転職など、記事の話題に合う選び方ガイド（見出しとタグが合うときだけ）
  const chooseGuide = chooseGuideForArticle(article);
  // 日本語の黙読はおよそ毎分600字
  const readMinutes = Math.max(1, Math.round(article.body.length / 600));
  // 「かんたんに読む」版（ある記事だけ、読み方を切り替えられる）
  const easy = article.easy?.trim() ?? "";
  const easyMinutes = Math.max(1, Math.round(easy.length / 500));
  const easyHtml = easy ? await renderMarkdown(easy, affiliates.map((a) => a.url)) : "";
  const html = await renderMarkdown(
    article.body,
    affiliates.map((a) => a.url),
  );
  // 第3章の前に広告、さらに後半の前に収益導線を挟む
  const [bodyTop, afterAd] = splitBeforeHeading(html, 3);
  const [bodyMid, bodyTail] = splitBeforeHeading(afterAd, 4);
  const headings = extractHeadings(article.body);
  // 読み終えた人に「次の1本」を大きく見せる。本文の途中にも1本だけ差し込む
  const nextReads = getNextReads(article, (a) => companyOf(a).slug);
  const nextSlugs = new Set(nextReads.map((n) => n.article.slug));
  const related = getRelatedArticles(article, 8).filter((a) => !nextSlugs.has(a.slug)).slice(0, 4);

  // 判定カードに見えている問いと答えを、そのまま FAQ の構造化データにする（AI検索の引用向け）
  const name = article.seoTitle?.split(/[｜|：:]/)[0]?.trim() || "この発表";
  const faq = verdict
    ? [
        { q: `${name}は今すぐ使える？`, a: `${STATUS_LABEL[verdict.status]}。${verdict.take}` },
        { q: `${name}は日本から使える？`, a: japanLabel(verdict) },
        { q: `${name}の料金は？`, a: verdict.price || "不明" },
        { q: `${name}は無料で試せる？`, a: freeLabel(verdict.free) },
        ...(verdict.audience ? [{ q: `${name}は誰に向いている？`, a: verdict.audience }] : []),
      ]
    : [];

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "NewsArticle",
      headline: article.title,
      description: article.description,
      // サイトで配信する写真（/img/、幅 1200px の JPEG）があれば先頭に置く（Google Discover の大きな画像。robots は max-image-preview:large）。
      // 写真が無い・外部の画像の記事は従来どおり見出し入りのカバーが先
      image: (isOwnImage(article.thumbnail) ? [article.thumbnail, coverPath(article)] : [coverPath(article), article.thumbnail])
        .filter(Boolean)
        .map((src) => (src!.startsWith("/") ? `${siteConfig.url}${src}` : src)),
      // サイソクに載せた日時（ニュースサイトマップ・フィードと同じ。元の発表日時は本文側に表示）
      datePublished: article.generatedAt || article.publishedAt,
      // 続報を足した記事は最終更新日時
      dateModified: lastModifiedOf(article),
      inLanguage: "ja",
      keywords: article.keywords.join(", "),
      mainEntityOfPage: `${siteConfig.url}/articles/${article.slug}`,
      author: authorJsonLd,
      // 編集方針と訂正方針（誰がどう責任を持つかを検索エンジンに示す）
      publishingPrinciples: `${siteConfig.url}/editorial-policy`,
      correctionsPolicy: `${siteConfig.url}/editorial-policy`,
      // AIを使った制作であることの開示（schema.org の backstory: 記事がどう作られたか）
      backstory: `生成AIで下書きを作成し、元の発表と照合してから公開しています。制作方法と事実確認の手順: ${siteConfig.url}/editorial-policy`,
      publisher: publisherJsonLd,
      isAccessibleForFree: true,
      articleSection: company.name,
      about: article.tags.slice(0, 5).map((tag) => ({ "@type": "Thing", name: tag })),
      // 音声アシスタント・AI検索が読み上げ／引用しやすい要約部分
      speakable: {
        "@type": "SpeakableSpecification",
        cssSelector: ["#points-heading + ol", "#verdict-heading + p"],
      },
      // web 記事は元記事を出典として明示する
      ...(web && sourceUrl
        ? {
            isBasedOn: sourceUrl,
            citation: {
              "@type": "CreativeWork",
              name: article.videoTitle,
              url: sourceUrl,
              publisher: { "@type": "Organization", name: sourceName },
            },
          }
        : {}),
    },
    ...(video
      ? [
          {
            "@context": "https://schema.org",
            "@type": "VideoObject",
            name: article.videoTitle,
            description: article.description,
            ...(article.thumbnail ? { thumbnailUrl: [article.thumbnail] } : {}),
            uploadDate: article.publishedAt,
            contentUrl: article.videoUrl,
            embedUrl: `https://www.youtube.com/embed/${article.videoId}`,
          },
        ]
      : []),
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        {
          "@type": "ListItem",
          position: 2,
          name: article.channel,
          item: `${siteConfig.url}/channels/${article.channelSlug}`,
        },
        { "@type": "ListItem", position: 3, name: article.title },
      ],
    },
    ...(faq.length > 0
      ? [
          {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            mainEntity: faq.map(({ q, a }) => ({
              "@type": "Question",
              name: q,
              acceptedAnswer: { "@type": "Answer", text: a },
            })),
          },
        ]
      : []),
  ];

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-8 sm:px-8 sm:py-10">
      <script
        type="application/ld+json"
        suppressHydrationWarning
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <nav aria-label="パンくず" className="flex flex-wrap items-center gap-1.5 text-meta text-muted">
            <Link href="/" className="hover:text-accent">
              ホーム
            </Link>
            <span aria-hidden>/</span>
            <Link href={`/companies/${company.slug}`} className="hover:text-accent">
              {company.name}
            </Link>
            <span aria-hidden>/</span>
            <Link href={`/channels/${article.channelSlug}`} className="hover:text-accent">
              {article.channel}
            </Link>
          </nav>

          <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta">
            <CompanyMark company={company} size={20} withName />
            <span className="rounded-sm border border-line-strong px-2 py-[1px] text-badge font-bold text-ink-soft">
              {isRoundup ? `まとめ ${article.sources?.length ?? 0}本` : article.sourceType === "web" ? "公開情報" : "動画紹介"}
            </span>
            {speedLabel(article) && (
              <span className="rounded-sm border border-live/40 px-2 py-[1px] text-badge font-bold text-live">
                {speedLabel(article)}
              </span>
            )}
            <span className="text-muted">
              <RelTime iso={article.publishedAt} className="index-num font-semibold text-ink" />
              <span className="index-num ml-1.5">（{formatDate(article.publishedAt)}）</span>
            </span>
            {article.generatedAt && formatDateTime(article.generatedAt) && (
              <span className="index-num text-muted">
                掲載: <time dateTime={article.generatedAt}>{formatDateTime(article.generatedAt)}</time>
              </span>
            )}
            <span className="index-num text-muted">読了 {readMinutes}分</span>
            {article.updatedAt && formatDateTime(article.updatedAt) && (
              <span className="index-num font-semibold text-ink-soft">
                最終更新: <time dateTime={article.updatedAt}>{formatDateTime(article.updatedAt)}</time>
              </span>
            )}
          </div>

          <Morph name={`title-${article.slug}`}>
            <h1 className="t-h1 headline mt-3">
              <Highlight text={article.title} />
            </h1>
          </Morph>

          <p className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-note leading-relaxed text-ink-soft">
            <span>
              制作：<Link href="/about" rel="author" className="font-semibold underline decoration-line-strong underline-offset-4 hover:text-accent">{siteConfig.author}</Link>
            </span>
            <Link href="/editorial-policy" className="underline decoration-line-strong underline-offset-4 hover:text-accent">
              編集方針・AI利用と事実確認
            </Link>
          </p>

          {/* 3秒でわかる要点 */}
          {article.highlights.length > 0 && (
            <section aria-labelledby="points-heading" className="mt-6 rounded-xl border-l-4 border-accent bg-surface p-5 sm:p-6">
              <h2 id="points-heading" className="t-h3">
                3秒でわかる要点
              </h2>
              <ol className="mt-4 grid gap-3.5">
                {article.highlights.map((highlight, index) => (
                  <li key={index} className="grid grid-cols-[24px_1fr] gap-3 text-desc font-medium leading-[1.7] text-ink">
                    <span className="index-num mt-0.5 grid size-6 place-items-center rounded-full bg-ink text-meta font-semibold text-paper">
                      {index + 1}
                    </span>
                    {highlight}
                  </li>
                ))}
              </ol>
            </section>
          )}
          {/* 「使える？」判定 */}
          {verdict && <VerdictCard verdict={verdict} judgedAt={verdict.judgedAt || article.generatedAt || article.publishedAt} />}
          {/* 料金早見表・イベントページへの案内（当てはまる記事だけ） */}
          <AssetLinks article={article} />

          {/* 読み方の切り替え（かんたん／専門家）。選んだ読み方は次の記事でも引き継ぐ */}
          {easyHtml && <ReadingMode easyMinutes={easyMinutes} expertMinutes={readMinutes} />}

          {/* 製品のまとめページへの案内（「〇〇とは」「料金」を知りたい読者の受け皿）。目立たせず1行だけ */}
          {product ? (
            <p className="mt-4 text-note">
              <Link
                href={`/products/${product.slug}`}
                className="font-bold text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
              >
                {product.name}のまとめページ →
              </Link>
            </p>
          ) : null}
          {howto && (
            <p className="mt-2 text-note">
              <Link
                href={`/guides/${howto.slug}`}
                className="font-bold text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
              >
                {howto.title} →
              </Link>
            </p>
          )}
          {!product && guide && (
            <Link
              href={`/guides/${guide.slug}`}
              className="group mt-4 flex items-center gap-3 rounded-2xl border border-line px-5 py-3.5 transition-colors hover:border-ink"
            >
              <span className="shrink-0 rounded-sm bg-ink px-2.5 py-0.5 text-badge font-bold text-paper">ガイド</span>
              <span className="min-w-0 text-note font-bold leading-snug text-ink group-hover:underline">
                {guide.name}とは？できること・料金・使い方を1ページで
              </span>
              <span aria-hidden className="ml-auto text-muted">→</span>
            </Link>
          )}

          {article.lede && (
            <p className="t-lead mt-6">
              {article.lede}
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-meta text-muted">
            {/* ステマ規制（景品表示法）に沿い、広告を含みうる全記事の冒頭で明示する */}
            <span className="rounded-sm bg-surface px-2 py-0.5 text-badge font-bold">
              本ページはプロモーションを含みます
            </span>
          </div>

          {/* 個別記事 → 同じ話題のまとめ記事へ */}
          {article.roundupSlug && (
            <Link
              href={`/articles/${article.roundupSlug}`}
              className="group mt-6 flex items-start gap-3 rounded-2xl bg-surface px-5 py-4 transition-colors hover:bg-surface-2"
            >
              <span className="mt-0.5 shrink-0 rounded-sm bg-ink px-2.5 py-0.5 text-badge font-bold text-paper">
                まとめ
              </span>
              <span className="min-w-0">
                <span className="block text-meta text-muted">この話題の全体像はこちら</span>
                <span className="t-card mt-0.5 block leading-snug group-hover:text-accent">
                  {article.roundupTitle}
                </span>
              </span>
            </Link>
          )}

          {/* ① 記事トップの広告枠 */}
          <AdSlot position="article-top" className="mt-6" />

          {/* まとめ記事: 統合本数を主役に */}
          {isRoundup && (
            <div className="mt-7 flex flex-wrap items-end gap-x-4 gap-y-1 rounded-3xl bg-surface px-6 py-5">
              <span className="index-num text-[3.5rem] font-semibold leading-[0.85] tracking-tighter text-roundup">
                {article.sources?.length ?? 0}
              </span>
              <span className="pb-1 text-note font-bold leading-snug text-muted">
                本の発表を1本に統合した記事です
              </span>
            </div>
          )}

          {/* 個別記事（公式ブログ・プレスリリース）: 元記事の画像と出典 */}
          {!isRoundup && web && (
            <figure className="mt-7 overflow-hidden rounded-3xl border border-line">
              {article.thumbnail && (
                <Morph name={`cover-${article.slug}`}>
                  <a
                    href={sourceUrl || undefined}
                    target="_blank"
                    rel="noopener"
                    className="group relative block aspect-video bg-surface-2"
                    aria-label={`出典「${article.videoTitle}」を${sourceName}で読む`}
                  >
                    <Thumb
                      src={article.thumbnail}
                      source={thumbSource(article)}
                      alt=""
                      fill
                      priority
                      sizes="(max-width: 1024px) 100vw, 860px"
                      className="object-cover transition-transform duration-500 ease-out group-hover:scale-[1.02]"
                    />
                  </a>
                </Morph>
              )}
              <ImageCredit credit={article.imageCredit} className="bg-surface px-5 pt-3" />
              <figcaption className="flex items-center gap-4 bg-surface px-5 py-4">
                <CompanyMark company={company} size={36} />
                <span className="min-w-0 flex-1">
                  <span className="block text-meta font-bold text-muted">出典: {sourceName}</span>
                  {sourceUrl ? (
                    <a
                      href={sourceUrl}
                      target="_blank"
                      rel="noopener"
                      className="mt-0.5 block text-[14.5px] font-bold leading-snug text-ink underline-offset-4 hover:text-accent hover:underline"
                    >
                      「{article.videoTitle}」
                    </a>
                  ) : (
                    <span className="mt-0.5 block text-[14.5px] font-bold leading-snug text-ink">「{article.videoTitle}」</span>
                  )}
                </span>
                {sourceUrl && (
                  <a
                    href={sourceUrl}
                    target="_blank"
                    rel="noopener"
                    className="hidden shrink-0 items-center gap-1.5 rounded-sm border border-line-strong px-3.5 py-1.5 text-badge font-bold text-ink-soft transition-colors hover:border-ink hover:text-ink sm:inline-flex"
                  >
                    元記事を読む
                  </a>
                )}
              </figcaption>
            </figure>
          )}

          {/* 個別記事: 動画（クリックで初めて YouTube を開く軽量表示） */}
          {!isRoundup && !web && (
            <div className="mt-7 overflow-hidden rounded-3xl bg-black">
              <Morph name={`cover-${article.slug}`}>
                <a
                  href={article.videoUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="group relative block aspect-video"
                  aria-label={`元動画「${article.videoTitle}」をYouTubeで見る`}
                >
                  <Thumb
                    src={article.thumbnail}
                    alt=""
                    fill
                    priority
                    sizes="(max-width: 1024px) 100vw, 860px"
                    className="object-cover opacity-90 transition-opacity group-hover:opacity-100"
                  />
                  {/* 元動画のサムネイルが消えてイメージ写真に替えた記事は、再生ボタンを出さない */}
                  {!stockPhoto && (
                  <span className="absolute inset-0 grid place-items-center">
                    <span className="grid h-14 w-20 place-items-center rounded-2xl bg-red-600/95 text-white shadow-lg transition-transform group-hover:scale-110">
                      <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                        <path d="M8 5v14l11-7z" />
                      </svg>
                    </span>
                  </span>
                  )}
                </a>
              </Morph>
              {stockPhoto && <ImageCredit credit={article.imageCredit} className="px-5 pt-3 !text-white/80" />}
              <p className="truncate px-5 py-3 text-note text-white/75">
                元動画: {article.videoTitle}
              </p>
            </div>
          )}


          <div id="article-flow" data-read-mode="expert">
          {easyHtml && (
            <div className="read-easy">
              <p className="mt-8 inline-flex items-center gap-2 rounded-sm bg-go-soft px-3 py-1 text-badge font-bold text-go">
                かんたん版：専門用語を使わずに説明しています
              </p>
              <article className="prose mt-5 max-w-[40em]" dangerouslySetInnerHTML={{ __html: easyHtml }} />
              {inlineOffer && (
                <div className="my-10 max-w-[46em]">
                  <RailRanking offer={inlineOffer} wide title={inlineOffer.headline} countImpression={false} />
                </div>
              )}
            </div>
          )}

          <div className="read-expert">
          {/* 章立て */}
          {headings.length > 1 && (
            <nav aria-labelledby="toc-heading" className="mt-6 rounded-3xl border border-line p-6">
              <h2 id="toc-heading" className="t-h3">
                この記事の章立て
              </h2>
              <ol className="mt-3 grid list-decimal gap-2 pl-5 marker:font-mono marker:text-meta marker:text-muted">
                {headings.map((heading) => (
                  <li key={heading.id}>
                    <a href={`#${heading.id}`} className="text-note font-medium text-ink-soft hover:text-accent">
                      {heading.text}
                    </a>
                  </li>
                ))}
              </ol>
            </nav>
          )}

          {/* 本文（前半） */}
          <article className="prose mt-10 max-w-[40em]" dangerouslySetInnerHTML={{ __html: bodyTop }} />
          {bodyMid && <InlineRead item={nextReads[2] ?? nextReads[1]} />}

          {/* ② 記事中盤の広告枠 */}
          {bodyMid && <AdSlot position="article-middle" className="my-10" />}

          {bodyMid && (
            <article className="prose max-w-[40em]" dangerouslySetInnerHTML={{ __html: bodyMid }} />
          )}

          {/* 本文に馴染ませる収益導線。関連ツールは本文の途中（読者がまだ読んでいる位置）に置く。
              記事末だけだと、ページの4分の3より下まで読んだ人にしか見えなかった */}
          {bodyTail && chooseGuide && <ChooseGuideNote guide={chooseGuide} className="my-10" />}

          {bodyTail && inlineOffer && (
            <div className="my-10 max-w-[46em]">
              <RailRanking offer={inlineOffer} wide id="offer-inline" title={inlineOffer.headline} />
              {buyingGuide && <CompareLink slug={buyingGuide.slug} name={guideShortName(buyingGuide)} className="flex lg:hidden" />}
            </div>
          )}
          {bodyTail && shopLink && <ShopLinks link={shopLink} className="my-10" />}
          {bodyTail && <CtaBanner genreSlug={article.genreSlug} slot="inline" className="my-10" />}

          {bodyTail && (
            <article className="prose max-w-[40em]" dangerouslySetInnerHTML={{ __html: bodyTail }} />
          )}
          </div>
          </div>

          {/* 出典（公式）・日時・確認方法・更新履歴 */}
          {!isRoundup && (
            <SourceRecord
              article={article}
              sourceUrl={sourceUrl}
              sourceName={sourceName}
              guide={
                howto
                  ? { href: `/guides/${howto.slug}`, label: howto.title }
                  : guide
                    ? { href: `/guides/${guide.slug}`, label: `${guide.name}とは？できること・料金・使い方` }
                    : product
                      ? { href: `/products/${product.slug}`, label: `${product.name}のまとめページ` }
                      : undefined
              }
            />
          )}

          {/* 読み終えた位置で、次の1本を大きく見せる */}
          <NextRead items={nextReads} />

          {/* まとめ記事が統合した元記事 */}
          {isRoundup && <RoundupSources sources={article.sources ?? []} />}

          {/* 動画のタイムテーブル */}
          {video && article.timetable.length > 0 && (
            <section aria-labelledby="timetable-heading" className="mt-12">
              <h2 id="timetable-heading" className="t-h2">
                動画のタイムテーブル
              </h2>
              <ul className="mt-4 divide-y divide-line overflow-hidden rounded-3xl bg-surface">
                {article.timetable.map((item, index) => (
                  <li key={index} className="grid grid-cols-[4.5rem_1fr] gap-4 px-5 py-3.5">
                    <a
                      href={`${article.videoUrl}&t=${toSeconds(item.time)}s`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="index-num text-meta font-semibold text-official hover:underline"
                    >
                      {item.time}
                    </a>
                    <span className="text-desc leading-relaxed text-ink-soft">{item.label}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {!bodyTail && chooseGuide && <ChooseGuideNote guide={chooseGuide} className="mt-12" />}

          {/* 記事末の主導線 */}
          <CtaBanner genreSlug={article.genreSlug} slot="primary" className="mt-12" />

          {/* 毎朝のメール配信と速報のプッシュ通知（また来てもらう導線） */}
          <NewsletterSignup variant="article" />
          <PushOptIn />

          {/* 本文が短く途中に置けなかった記事だけ、記事末に出す */}
          {!bodyTail && inlineOffer && (
            <div className="mt-12 max-w-[46em]">
              <RailRanking offer={inlineOffer} wide id="offer-inline" title={inlineOffer.headline} />
              {buyingGuide && <CompareLink slug={buyingGuide.slug} name={guideShortName(buyingGuide)} className="flex lg:hidden" />}
            </div>
          )}
          {!bodyTail && shopLink && <ShopLinks link={shopLink} className="mt-12" />}

          {article.tags.length > 0 && (
            <div className="mt-12 flex flex-wrap gap-2">
              {article.tags.map((tag) => (
                <Link
                  key={tag}
                  href={tagHref(tag)}
                  className="rounded-md bg-surface px-3.5 py-1.5 text-meta font-medium text-ink-soft hover:bg-surface-2 hover:text-accent"
                >
                  #{tag}
                </Link>
              ))}
            </div>
          )}

          <p className="mt-10 rounded-2xl bg-surface px-5 py-4 text-note leading-relaxed text-muted">
            {article.disclaimer ||
              (web
                ? `本記事は${sourceName}の発表内容をAIが解析して自動生成したものです。誤りが含まれる可能性があるため、正確な情報は元記事をご確認ください。`
                : "本記事は元動画の映像・音声をAIが解析して自動生成したものです。誤りが含まれる可能性があるため、正確な情報は公式発表をご確認ください。")}
            {sourceUrl && (
              <a
                href={sourceUrl}
                target="_blank"
                rel={web ? "noopener" : "noopener noreferrer"}
                className="mx-1 text-accent underline"
              >
                {web ? "元記事を読む" : "元動画を見る"}
              </a>
            )}
            （解析モデル: {article.model}）
            <span className="mt-1.5 block">
              制作方法と事実確認の手順は
              <Link href="/editorial-policy" className="mx-1 text-accent underline">
                編集方針
              </Link>
              をご覧ください。誤りのご指摘は
              <Link href="/contact" className="mx-1 text-accent underline">
                お問い合わせ
              </Link>
              から受け付けています。
            </span>
          </p>

          {related.length > 0 && (
            <section aria-labelledby="related-heading" className="mt-16">
              <h2 id="related-heading" className="t-h2 mb-5 border-b border-line-strong pb-3">
                関連記事
              </h2>
              <div className="grid gap-3.5 sm:grid-cols-2">
                {related.map((item) => (
                  <ArticleCard key={item.slug} article={item} quickImage />
                ))}
              </div>
            </section>
          )}
        </div>

        {/* ③ サイドバー（最上部に広告枠） */}
        <aside className="min-w-0">
          {offer ? (
            // 商品枠だけを右側に置き、読み進めても常に見える位置に付いてくる。
            // 以前は最新記事やタグも並べていて背が高く、最後までスクロールしないと追従しなかった
            <div className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto lg:[scrollbar-width:thin]">
              <RailRanking offer={offer} />
              {/* PC は右側の商品枠の下、スマホは本文中の商品枠の下（1画面に1つだけ） */}
              {buyingGuide && <CompareLink slug={buyingGuide.slug} name={guideShortName(buyingGuide)} className="hidden lg:flex" />}
            </div>
          ) : (
            <Sidebar current={article} />
          )}
        </aside>
      </div>

      {/* スマホ：読み進めると画面下にイチオシを出す */}
      {offer && <MobilePrBar item={offer.items[0]} watchId="offer-inline" />}
    </div>
  );
}

/** 比較ガイドへの1行の案内（商品枠の直下） */
/** 記事の話題に合う選び方ガイドへの案内（本文と同じ調子の囲み1つ） */
function ChooseGuideNote({ guide, className = "" }: { guide: ChooseGuide; className?: string }) {
  return (
    <aside data-funnel-pos="article" aria-label="関連する選び方ガイド" className={`max-w-[40em] border-l-[3px] border-accent bg-surface px-4 py-3.5 ${className}`}>
      <p className="text-meta font-bold text-muted">あわせて読みたい</p>
      <Link href={`/choose/${guide.slug}`} className="mt-1 block text-[16px] font-bold leading-snug text-ink hover:text-accent hover:underline">
        {guide.articleLabel}
      </Link>
      <p className="mt-1 text-note leading-[1.7] text-ink-soft">{guide.articleNote}</p>
    </aside>
  );
}

function CompareLink({ slug, name, className = "" }: { slug: string; name: string; className?: string }) {
  return (
    <Link
      href={`/compare/${slug}`}
      className={`group mt-2.5 items-center gap-2 rounded-xl border border-line px-3.5 py-2.5 text-meta transition-colors hover:border-ink ${className}`}
    >
      <span className="shrink-0 rounded-sm bg-ink px-2 py-0.5 text-badge font-bold text-paper">比較</span>
      <span className="min-w-0 font-bold leading-snug text-ink group-hover:underline">
        {name}を選ぶなら：比較ガイドを見る
      </span>
      <span aria-hidden className="ml-auto text-muted">→</span>
    </Link>
  );
}

/** "12:34" / "1:02:03" → 秒 */
function toSeconds(time: string): number {
  const parts = time.split(":").map((p) => Number.parseInt(p, 10));
  if (parts.some(Number.isNaN)) return 0;
  return parts.reduce((total, part) => total * 60 + part, 0);
}
