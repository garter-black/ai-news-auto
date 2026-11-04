import { articleTitle } from "@/lib/headline";
import { canonicalPathOf, isIndexable } from "@/lib/indexing";
import type { Metadata } from "next";
import Thumb from "@/components/Thumb";
import ImageCredit from "@/components/ImageCredit";
import Link from "next/link";
import { notFound } from "next/navigation";
import GoneView, { GONE_TITLE } from "@/components/GoneView";
import { getRetired } from "@/lib/retired";
import AdSlot from "@/components/AdSlot";
import MobilePrBar from "@/components/rail/MobilePrBarLazy";
import RailRanking from "@/components/rail/RailRanking";
import ArticleCard from "@/components/ArticleCard";
import CompanyMark, { ArticleMark } from "@/components/CompanyMark";
import CtaBanner from "@/components/CtaBanner";
import RelTime from "@/components/RelTime";
import NextRead from "@/components/NextRead";
import NewsletterSignup from "@/components/NewsletterSignup";
import PushOptIn from "@/components/PushOptIn";
import InlineRead from "@/components/InlineRead";
import ReadingMode from "@/components/ReadingMode";
import VerdictCard from "@/components/VerdictCard";
import WorkMoneyCard from "@/components/WorkMoneyCard";
import { concreteWorkMoney, workMoneyHeading, workMoneyOf } from "@/lib/work-money";
import AssetLinks from "@/components/assets/AssetLinks";
import SourceRecord from "@/components/SourceRecord";
import { OTHER, companyOf, companyPageSlugs, sourceBadge, thumbSource } from "@/lib/companies";
import { guideForArticle, guideHref } from "@/lib/guides";
import { howtoForArticle } from "@/lib/howto";
import { productForArticle, supersedesGuide } from "@/lib/products";
import { buyingGuideForTopic, guideShortName } from "@/lib/buying-guides";
import { chooseGuideForArticle } from "@/lib/choose";
import type { ChooseGuide } from "@/lib/choose-data";
import { STATUS_LABEL, freeLabel, isUnknownValue, japanLabel, verdictKind, verdictOf } from "@/lib/verdict";
import { guideLinksForArticle, insertAfterPricingSection } from "@/lib/guide-links";
import ArticleFaq from "@/components/ArticleFaq";
import TodayActions from "@/components/TodayActions";
import ShareButtons from "@/components/ShareButtons";
import { offerPlanForArticle } from "@/lib/offers";
import { getCta } from "@/lib/monetization";
import { inlineReadForArticle, readNextForArticle } from "@/lib/read-next";
import ReadNextBox from "@/components/ReadNextBox";
import ArticleVideo from "@/components/ArticleVideo";
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
  reviewedDateLabel,
  sourceHref,
  sourceLabel,
  speedLabel,
  tagHref,
} from "@/lib/articles";
import { Highlight } from "@/lib/highlight";
import { isOwnImage } from "@/lib/thumb-url";
import {
  renderMarkdown,
  splitBeforeHeading,
} from "@/lib/markdown";
import { siteConfig } from "@/site.config";
import { cleanSections, cleanTake, dropOverviewSections, dropSummarySections, retitleHtml, stripSummaryOpener, titleSlugLinks, tocOf } from "@/lib/article-sections";
import ArticleProse from "@/components/ArticleProse";
import CalculatorBox from "@/components/calc/CalculatorBox";
import { CALC_SLOT, insertFigures, markCalculatorSlot, renderChart, type ChartFigure } from "@/lib/charts";
import { insertBodyImages } from "@/lib/body-images";
import { calculatorForArticle } from "@/lib/calculators";
import { getFx, getPlanProduct } from "@/lib/plan-prices";
import PriceTable from "@/components/PriceTable";
import { hasPriceBlock, markPriceSlot, pricingForArticle, pricingRelevant, PRICE_SLOT, withPriceSlot } from "@/lib/price-slot";
import { authorJsonLd, publisherJsonLd } from "@/lib/publisher";

type Props = { params: Promise<{ slug: string }> };

// 公開中の記事はすべてビルド時に静的化する（台帳 W29）。
// 以前は新しい150本だけで、残りはデプロイのたびに初回アクセスで作り直していた。ISR のキャッシュはビルドごとに空になるため、
// 古い記事はほぼ毎回「作りながら返す」状態で、ロボットや画面の点検がまとめて開くと 1 つの isolate に描画が積み上がり、
// 1ページ 30〜90 秒待たされた（2026-11-03 17:55〜18:03 UTC、Workers の wall 時間 p99.9 が 30〜90 秒・CPU は数百 ms）。
// 記事が無い slug は notFound() で 404。デプロイなしで積んだ新しい記事は次のデプロイで出る
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  return getAllArticles().map((article) => ({ slug: article.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const article = getArticle(slug);
  // 取り下げた記事（content/retired.json）。cloudflare/worker.ts がこのページを 410 で返す（台帳 W22）
  if (!article) return getRetired(slug) ? { title: GONE_TITLE, robots: { index: false, follow: true } } : {};

  const url = `/articles/${article.slug}`;
  const cover = coverPath(article);
  // 題名は1つだけ（lib/headline.ts の articleTitle）。タブ・og:title・X のカード・画像の alt・見出し（h1）・構造化データで同じ（台帳 A29）。
  // 以前は <title> と画像の alt に検索向けの seoTitle（「〜とは？日本で使える？」）を使い、見出しと別ものになっていた（2026-11-03 の点検 10）
  const title = articleTitle(article);
  return {
    title,
    description: article.description,
    keywords: article.keywords,
    // 同じ発表の重複記事は、代表の記事を正式なページにする（lib/indexing.ts）
    alternates: {
      canonical: canonicalPathOf({
        slug: article.slug,
        duplicateOf: article.duplicateOf && getArticleSummary(article.duplicateOf) ? article.duplicateOf : undefined,
      }),
    },
    // 薄くなりやすい記事はサイトでは読めるが、検索には出さない（lib/indexing.ts）
    ...(isIndexable({ ...article, duplicate: getArticleSummary(article.slug)?.duplicate }) ? {} : { robots: { index: false, follow: true } }),
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale,
      type: "article",
      url,
      title,
      description: article.description,
      publishedTime: article.generatedAt || article.publishedAt,
      modifiedTime: lastModifiedOf(article),
      tags: article.tags,
      // SNSのカードは日本語見出し入りの独自カバー。無い記事は元動画（元記事）の画像
      images: cover
        ? [{ url: cover, width: 1200, height: 630, alt: title }]
        : article.thumbnail
          ? [{ url: article.thumbnail, width: 1280, height: 720 }]
          : undefined,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description: article.description,
      images: cover ? [cover] : article.thumbnail ? [article.thumbnail] : undefined,
    },
  };
}

export default async function ArticlePage({ params }: Props) {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) {
    // 取り下げた記事は、サイトの見た目のまま「掲載を終了しました」と近い記事を出す（状態コード 410 は cloudflare/worker.ts が付ける。台帳 W22）
    const retired = getRetired(slug);
    if (retired) return <GoneView entry={retired} />;
    notFound();
  }

  const isRoundup = article.type === "roundup";
  // 検索に出さない記事（薄い・主題外・重複・他人の動画の要約など）には AdSense の広告枠を出さない。
  // 「有用性の低いコンテンツ」「複製されたコンテンツ」の画面に広告を載せないため（AdSense のパブリッシャー ポリシー）
  const showAds = isIndexable({ ...article, duplicate: getArticleSummary(article.slug)?.duplicate });
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
  const guide = guideMatch && !supersedesGuide(guideMatch.slug) ? guideMatch : undefined;
  // 題材の使い方ガイド（「料金・無料でどこまで」を知りたい読者の受け皿）
  const howto = guide ? undefined : howtoForArticle(article);
  const verdict = verdictOf(article);
  const cardKind = verdictKind(article);
  // 同じ発表の重複記事（scripts/story.py の duplicateOf）は、代表の記事へ案内する（検索では代表だけを出す）
  const mainArticle =
    article.duplicateOf && article.duplicateOf !== article.slug ? getArticleSummary(article.duplicateOf) : undefined;
  // 「仕事・お金」の欄・よくある質問も、本文と同じく答えになっていない文を除き、「〜しましょう」は落ち着いた言い方にする（lib/article-sections.ts）
  const workMoneyRaw = workMoneyOf(article.workMoney);
  const workMoneyPoints = (workMoneyRaw?.points ?? []).map(cleanTake).filter(Boolean);
  // 記事ごとの中身（数字・名前）がある点だけ。要点・リードのくり返しと一般論は外し、残らなければ欄ごと出さない（台帳 A28）
  const workMoney = concreteWorkMoney(
    workMoneyRaw && workMoneyPoints.length ? { ...workMoneyRaw, points: workMoneyPoints } : null,
    [...article.highlights, article.lede ?? ""],
  );
  // 同じ製品の料金・使い方ガイドへの固定の案内（要点のすぐ下と、本文の料金の章の下。lib/guide-links.ts）
  const guideLinks = guideLinksForArticle(article);
  const pricingLink = guideLinks.find((link) => link.kind === "pricing");
  const articleFaq = (article.faq ?? []).map((row) => ({ ...row, a: cleanTake(row.a ?? "") })).filter((row) => row.q && row.a);
  const todayActions = article.todayActions ?? [];
  // 広告枠は記事との関連の点数が基準を超えた商品だけを、関連の高い順に出す（lib/offers.ts）。
  // 右側（1〜3位）と本文中（4〜6位）で同じ商品は出さない。
  // 各枠は候補の中から、この端末でまだ見ていない商品を先に3つ出す（RankingBody）
  const { offer: offerAll, rail: offer, inline: inlineOffer } = offerPlanForArticle(article);
  // 商品枠の話題に比較ガイドがあれば、商品枠のすぐ下に1行だけ案内する
  const buyingGuide = buyingGuideForTopic(offerAll?.topic);
  // 製品を扱う記事だけ、本文中の商品枠のそばに「Amazon / 楽天で見る」を1行（右側には出さない）
  const shopLink = shopLinkForArticle(article);
  // 回線・転職など、記事の話題に合う選び方ガイド（見出しとタグが合うときだけ）
  const chooseGuide = chooseGuideForArticle(article);
  // 記事に合う自前のガイド・比較ページ（基準点以上のときだけ）。同じページに既に出ているガイドは選ばない
  const shownGuides = [
    howto && `/guides/${howto.slug}`,
    !product && guide && `/guides/${guide.slug}`,
    chooseGuide && `/choose/${chooseGuide.slug}`,
    buyingGuide && `/compare/${buyingGuide.slug}`,
  ].filter((href): href is string => Boolean(href));
  // 広告枠は全記事に出す（合う話題が無ければジャンルの既定の広告）。ガイドは記事に合うものがあるときだけ、広告に添えて出す
  const readNext = readNextForArticle(article, shownGuides).find((link) => link.kind !== "category" || !offer);
  // 本文中は、選び方ガイドの囲みが無いときだけ（同じ見た目の囲みを並べない）
  const readNextInline = !chooseGuide ? readNext : undefined;
  // 右側に広告が無いときは右側の先頭にも出す。PC は右側、スマホは本文中（1画面に1つだけ）
  const readNextRail = !offer ? readNext : undefined;
  const readNextInlineClass = readNextRail && readNextInline ? "lg:hidden" : "";
  // 日本語の黙読はおよそ毎分600字

  const reviewedLabel = reviewedDateLabel(article);
  // 「かんたんに読む」版（ある記事だけ、読み方を切り替えられる）
  // 中身なしの章・つなぎの文を除き（lib/article-sections.ts。scripts/section_gate.py と同じ規則）、
  // 決まった型の見出しは記事ごとの見出し（sectionTitles）か普通の見出しに言い換えて出す
  // 要約版の最初の「ひとことで言うと（要点）」は、上の「この記事のポイント」と同じ役目なので出さない（台帳 A28）
  // 頭の「要約すると、」はリードの言い直しなので外す（台帳 A19。新しい記事は書き出す前に scripts/article_style.py が外す）
  const easyClean = article.easy?.trim()
    ? cleanSections(stripSummaryOpener(article.easy.trim(), article.lede ?? ""), article.sectionTitles?.easy).markdown
    : "";
  const easy = article.highlights.length > 0 ? dropSummarySections(easyClean) : easyClean;
  const easyMinutes = Math.max(1, Math.round(easy.length / 500));
  const easyHtml = easy ? retitleHtml(await renderMarkdown(easy, affiliates.map((a) => a.url)), "easy", article.sectionTitles, "easy") : "";
  // 古い記事の「## 概要」の章は、リードと「この記事のポイント」のくり返しなので出さない（台帳 A19。新しい記事は門で作らせない）
  // リンクの文字が slug のままの記事へのリンクは、リンク先の題名にする（台帳 A36）
  const bodyOpened = stripSummaryOpener(titleSlugLinks(article.body, (s) => getArticleSummary(s)?.title), article.lede ?? "");
  const body = cleanSections(
    article.lede && article.highlights.length > 0 ? dropOverviewSections(bodyOpened) : bodyOpened,
    article.sectionTitles?.body,
  ).markdown;
  const readMinutes = Math.max(1, Math.round(body.length / 600));
  // 料金の章に差し込んだ表は、毎日更新の料金表（と同じ段の他社比較）に差し替える（lib/price-slot.ts）。
  // 同じ製品の料金の段の図（charts の ladder）を出す記事では、表は図のすぐ下に「詳しい表」として出し、同じ数字を2か所に並べない
  const pricing = pricingForArticle(article);
  const fx = getFx();
  const chartFigures = (article.charts ?? [])
    .filter((chart) => chart.kind !== "ladder" || pricingRelevant(article))
    .map((chart) => renderChart(chart, getPlanProduct, fx))
    .filter((figure): figure is ChartFigure => figure !== null);
  const ladderChart = Boolean(
    pricing && hasPriceBlock(body) && (article.charts ?? []).some((c) => c.kind === "ladder" && c.product === pricing.product) &&
      chartFigures.some((f) => f.kind === "ladder"),
  );
  const figures = ladderChart ? chartFigures.map((f) => (f.kind === "ladder" ? { ...f, html: f.html + PRICE_SLOT } : f)) : chartFigures;
  const html = markPriceSlot(
    await renderMarkdown(
      // 料金表を出さない記事（料金が話題でない・別の製品の表）では、本文 JSON に残った作った日の表も出さない（台帳 A32）
      insertAfterPricingSection(pricing ? withPriceSlot(body, !ladderChart) : withPriceSlot(body, false), pricingLink),
      affiliates.map((a) => a.url),
    ),
  );
  const priceBox =
    pricing && (ladderChart || html.includes(PRICE_SLOT)) ? (
      <div className="my-8 grid max-w-[46em] gap-6">
        <PriceTable product={pricing.product} title={ladderChart ? "プランごとの詳しい料金と上限" : undefined} />
        {pricing.compare && <PriceTable compare={pricing.compare.tier} kind={pricing.compare.kind} title={pricing.compare.kind === "api" ? "同じクラスの他社モデル" : "同じ価格帯の他社プラン"} />}
      </div>
    ) : undefined;
  // 記事の数字の図（記事 JSON の charts 欄。lib/charts.ts）を該当する章の下に差し込み、
  // 記事に合う計算機（lib/calculators.ts）の置き場所の印を、合う章の終わりに入れる
  const calc = calculatorForArticle(article);
  // 見出しの言い換えは、章名で位置を探す差し込み（図・計算機）が終わってから
  // 本文に置く公式の写真・図（出典つき・1〜2枚。lib/body-images.ts）
  const bodyHtml = retitleHtml(
    markCalculatorSlot(insertBodyImages(insertFigures(html, figures), article.bodyImages ?? [], article.title), calc?.heading ?? null),
    "body",
    article.sectionTitles,
  );
  const calcInline = Boolean(calc && bodyHtml.includes(CALC_SLOT));
  const calcBox = calc ? <CalculatorBox calc={calc} className="my-10" /> : null;
  // 第3章の前に広告、さらに後半の前に収益導線を挟む
  const [bodyTop, afterAd] = splitBeforeHeading(bodyHtml, 3);
  const [bodyMid, bodyTail] = splitBeforeHeading(afterAd, 4);
  // 本文中の PR は1か所だけ（PC は右側の1〜3位＋本文中1つ、スマホは本文中1つ＋ページ下の1〜3位の最大2つ）。
  // 同じページに PR の囲みが3つ以上並ぶと広告だらけに見えるため（2026-11-03 監査）。優先順:
  // 本文中の4〜6位（Amazon/楽天の1行は同じ囲みの中）→ Amazon/楽天の1行だけ → 収益導線（monetization.json）の中盤 → 記事末
  const inlineCta = bodyTail && !inlineOffer && !shopLink && getCta(article.genreSlug, "inline") ? "inline" : undefined;
  const primaryCta = !inlineOffer && !shopLink && !inlineCta && getCta(article.genreSlug, "primary") ? "primary" : undefined;
  const hasInlinePr = Boolean(inlineOffer || shopLink || inlineCta || primaryCta);
  const headings = tocOf(body, article.sectionTitles);
  // 読み終えた人に「次の1本」を大きく見せる。本文の途中にも1本だけ差し込む
  const nextReads = getNextReads(article, (a) => companyOf(a).slug);
  const nextSlugs = new Set(nextReads.map((n) => n.article.slug));
  // 本文途中の「あわせて読みたい」は、同じ話題・同じ企業の直近30日の記事だけ（基準点に届かなければ出さない）。
  // 記事末の「次に読むならこれ」の1本目とは重ねない
  const inlineRead = inlineReadForArticle(article, getAllArticles(), (a) => companyOf(a).slug, nextReads.slice(0, 1).map((n) => n.article.slug));
  const related = getRelatedArticles(article, 8).filter((a) => !nextSlugs.has(a.slug)).slice(0, 4);

  // 判定カードに見えている問いと答えを、そのまま FAQ の構造化データにする（AI検索の引用向け）
  // 「〇〇とは？…」型の題は「とは」「？」の前までを名前にする。長すぎる名前は問いが崩れるので「この発表」にする
  const titleName = article.seoTitle?.split(/[｜|：:？?]|とは/)[0]?.trim() ?? "";
  const name = titleName && titleName.length <= 30 ? titleName : "この発表";
  // 本文の「よくある質問」（記事に表示している問いと答え）があればそれを使う。
  // Google の FAQ のリッチリザルトは 2026-05 に終了したが、ページに見えている Q&A の印は他の検索・AI の答えに使われうるので残す
  const faq = articleFaq.length >= 3
    ? articleFaq.map((row) => ({ q: row.q, a: row.a }))
    : verdict && cardKind === "product"
    ? [
        { q: `${name}は今すぐ使える？`, a: `${STATUS_LABEL[verdict.status]}。${cleanTake(verdict.take ?? "")}` },
        { q: `${name}は日本から使える？`, a: japanLabel(verdict) },
        { q: `${name}の料金は？`, a: verdict.price || "不明" },
        { q: `${name}は無料で試せる？`, a: freeLabel(verdict.free) },
        ...(verdict.audience ? [{ q: `${name}は誰に向いている？`, a: verdict.audience }] : []),
        // 答えが「記載なし」「不明」の問いは載せない（答えになっていない Q&A は検索・AI の引用で逆効果）
      ].filter((row) => row.a && !/^(記載なし|不明|未定)/.test(row.a) && !isUnknownValue(row.a))
    : [];

  // パンくず。企業が決まらない記事（ビジネス・お金の記事の多く）に「その他」を出さず、ジャンルを先に置く
  const crumbs = [
    ...(article.genreSlug && article.genre ? [{ href: `/genres/${article.genreSlug}`, label: article.genre }] : []),
    // 企業ページがある会社だけ（ガジェットだけの会社はページが無く 404 になる。台帳 W3）
    ...(company.slug !== OTHER.slug && companyPageSlugs(getAllArticles()).has(company.slug) ? [{ href: `/companies/${company.slug}`, label: company.name }] : []),
    { href: `/channels/${article.channelSlug}`, label: article.channel },
  ];
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "NewsArticle",
      headline: articleTitle(article),
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
      correctionsPolicy: `${siteConfig.url}/corrections`,
      // AIを使った制作であることの開示（schema.org の backstory: 記事がどう作られたか）
      backstory: `${reviewedLabel ? `生成AIで下書きを作成し、元の発表と自動で照合してから公開しました。${reviewedLabel}に編集部が記事を読んで確認しています。` : "生成AIで下書きを作成し、元の発表と自動で照合してから公開しています。"}制作方法と事実確認の手順: ${siteConfig.url}/editorial-policy`,
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
      // 画面のパンくず（下の nav）と同じ並び: ホーム → ジャンル → 企業（決まった企業のときだけ）→ 発信元 → 記事
      itemListElement: [
        { name: "ホーム", item: siteConfig.url },
        ...crumbs.map((c) => ({ name: c.label, item: `${siteConfig.url}${c.href}` })),
        { name: articleTitle(article) },
      ].map((entry, i) => ({ "@type": "ListItem", position: i + 1, ...entry })),
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
            {crumbs.map((c) => (
              <span key={c.href} className="contents">
                <span aria-hidden>/</span>
                <Link href={c.href} className="hover:text-accent">
                  {c.label}
                </Link>
              </span>
            ))}
          </nav>

          <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta">
            <ArticleMark company={company} genre={article.genre} size={20} />
            <span className="rounded-sm border border-line-strong px-2 py-[1px] text-badge font-bold text-ink-soft">
              {/* 報道機関の記事は「報道」、企業・官公庁の発表は「公式発表」（台帳 W12） */}
              {isRoundup ? `まとめ ${article.sources?.length ?? 0}本` : video ? "動画紹介" : sourceBadge(article)}
            </span>
            {speedLabel(article) && (
              <span className="rounded-sm border border-live/40 px-2 py-[1px] text-badge font-bold text-live">
                {speedLabel(article)}
              </span>
            )}
            {/* 表示後に「3時間前」「昨日 13:55」などへ置き換わる。新しい記事は置き換え後の幅を先に取っておき、行の折り返しが変わって下の要点が押し下げられる（CLS）のを防ぐ。
                幅は全体（発表＋経過時間＋日付）で取る。経過時間だけに取ると「12時間前　　（11/03）」と間が空いて見えた（2026-11-03 の画面の点検）。
                「発表」と付けるのは、すぐ右の「掲載:」（サイソクに載せた時刻）と取り違えないため */}
            <span className={`text-muted ${isRecent(article.publishedAt) ? "inline-block min-w-[17em]" : ""}`}>
              発表 <RelTime iso={article.publishedAt} className="index-num font-semibold text-ink" />
              <span className="index-num">（{formatDate(article.publishedAt)}）</span>
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
              <Highlight text={articleTitle(article)} />
            </h1>
          </Morph>

          <p className="mt-4 flex flex-wrap gap-x-5 gap-y-1 text-note leading-relaxed text-ink-soft">
            <span>
              制作：<Link href="/about" rel="author" className="font-semibold underline decoration-line-strong underline-offset-4 hover:text-accent">{siteConfig.author}</Link>
            </span>
            <Link href="/editorial-policy" className="underline decoration-line-strong underline-offset-4 hover:text-accent">
              編集方針・AI利用と事実確認
            </Link>
            {/* 編集部（人）が読んだ記事だけ（/review。記録は記事 JSON の review）。確認していない記事には何も出さない */}
            {reviewedLabel && (
              <span className="font-semibold text-ink">
                <span aria-hidden>✓ </span>編集部が確認済み（{reviewedLabel}）
              </span>
            )}
          </p>

          {/* この記事のポイント */}
          {article.highlights.length > 0 && (
            <section aria-labelledby="points-heading" className="mt-6 rounded-xl border-l-4 border-accent bg-surface p-5 sm:p-6">
              <h2 id="points-heading" className="t-h3">
                この記事のポイント
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
          {/* シェア（要点を読んだ直後。小さく1行） */}
          <ShareButtons url={`${siteConfig.url}/articles/${article.slug}`} title={articleTitle(article)} className="mt-4" />
          {/* 同じ製品の料金・使い方ガイド（あるときだけ。要点のすぐ下に固定） */}
          {guideLinks.length > 0 && (
            <nav aria-label="この製品のガイド" className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-note">
              {guideLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="font-bold text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                >
                  {link.kind === "pricing" ? "料金ガイド：" : link.kind === "howto" ? "使い方ガイド：" : "ガイド："}
                  {link.label} →
                </Link>
              ))}
            </nav>
          )}
          {/* 解説動画（SNS に出した記事だけ。押したときだけ読み込む） */}
          {article.video?.src ? <ArticleVideo video={article.video} /> : null}
          {/* 「使える？」判定 */}
          {verdict && (
            <VerdictCard
              verdict={verdict}
              cardKind={cardKind}
              judgedAt={verdict.judgedAt || article.generatedAt || article.publishedAt}
            />
          )}
          {/* 仕事・お金への影響（記事ごとの中身があるときだけ。見出しはニュースの種類に合わせる） */}
          {workMoney && (
            <WorkMoneyCard
              workMoney={workMoney}
              heading={workMoneyHeading(article.newsType, article.genreSlug, article.title, article.tags)}
              money={article.genreSlug === "investment" || article.genreSlug === "real-estate" || article.newsType === "earnings"}
            />
          )}
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
          {howto && !guideLinks.some((link) => link.href === `/guides/${howto.slug}`) && (
            <p className="mt-2 text-note">
              <Link
                href={`/guides/${howto.slug}`}
                className="font-bold text-ink-soft underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
              >
                {howto.title} →
              </Link>
            </p>
          )}
          {!product && guide && !guideLinks.some((link) => link.href === `/guides/${guide.slug}`) && (
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

          {mainArticle && (
            <p className="mt-4 rounded-sm border border-line bg-surface px-4 py-3 text-note text-ink-soft">
              同じ話題をまとめた記事があります：
              <Link href={`/articles/${mainArticle.slug}`} className="ml-1 font-bold text-accent underline underline-offset-4">
                {mainArticle.title}
              </Link>
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
          {showAds && <AdSlot position="article-top" className="mt-6" />}

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
                    {/* 写真は要点・判定カードの下（スマホで約1,700px、PCで約1,350px下）にあり、最初の画面の LCP は見出しの文字。先読み・高優先度にすると見出しの表示に要る CSS・JS と回線を取り合うので、近づいてから読む */}
                    <Thumb
                      src={article.thumbnail}
                      source={thumbSource(article)}
                      cover={coverPath(article)}
                      alt=""
                      fill
                      sizes="(max-width: 639px) calc(100vw - 40px), (max-width: 1023px) calc(100vw - 64px), 820px"
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
                  {/* 上の写真と同じく最初の画面の外なので、近づいてから読む */}
                  <Thumb
                    src={article.thumbnail}
                    cover={coverPath(article)}
                    alt=""
                    fill
                    sizes="(max-width: 639px) calc(100vw - 40px), (max-width: 1023px) calc(100vw - 64px), 820px"
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


          {/* かんたん版と通常版は両方ともサーバーで描画し、読み方の切り替え（components/ReadingMode.tsx）で片方を隠す。
              本文中の PR の囲み・「あわせて読みたい」は、どちらの版にも入れず、2つの版のあいだ（通常版の後半の前）に1つだけ置く。
              かんたん版ではかんたん版の直後、通常版では後半の前に見える（同じ広告の枠をページに2つ置かない。2026-11-03） */}
          <div id="article-flow" data-read-mode="expert">
          <div className="read-expert">
          {/* 章立て（本文の前。章が3つ以下の短い記事には出さない。台帳 A28） */}
          {headings.length > 3 && (
            <nav aria-labelledby="toc-heading" className="mt-6 rounded-3xl border border-line p-6">
              <h2 id="toc-heading" className="t-h3">
                目次
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
          </div>
          {easyHtml && (
            <div className="read-easy">
              <p className="mt-8 inline-flex items-center gap-2 rounded-sm bg-go-soft px-3 py-1 text-badge font-bold text-go">
                要約版：専門用語を控えて説明しています
              </p>
              <article className="prose mt-5 max-w-[40em]" dangerouslySetInnerHTML={{ __html: easyHtml }} />
            </div>
          )}

          <div className="read-expert">

          {/* 本文（前半） */}
          <ArticleProse className="prose mt-10 max-w-[40em]" html={bodyTop} slot={calcInline ? calcBox : undefined} priceSlot={priceBox} />
          {bodyMid && inlineRead && <InlineRead item={{ article: inlineRead, reason: "同じ話題の記事" }} />}

          {/* ② 記事中盤の広告枠 */}
          {showAds && bodyMid && <AdSlot position="article-middle" className="my-10" />}

          {bodyMid && (
            <ArticleProse className="prose max-w-[40em]" html={bodyMid} slot={calcInline ? calcBox : undefined} priceSlot={priceBox} />
          )}

          {/* 本文に馴染ませる収益導線。関連ツールは本文の途中（読者がまだ読んでいる位置）に置く。記事末だけだと、ページの4分の3より下まで読んだ人にしか見えなかった */}
          {bodyTail && chooseGuide && <ChooseGuideNote guide={chooseGuide} className="my-10" />}
          </div>

          {/* 本文中の PR（4〜6位の商品枠。Amazon / 楽天の1行は同じ囲みの中）と「あわせて読みたい」。ページに1つだけ */}
          {bodyTail && inlineOffer && (
            <div className="my-10 max-w-[46em]">
              <RailRanking offer={inlineOffer} wide id="offer-inline" title={inlineOffer.headline} />
              {/* Amazon / 楽天の1行は、本文中の PR の囲みと同じ場所にまとめる（PR を別々に並べない） */}
              {shopLink && <ShopLinks link={shopLink} className="mt-3" />}
              {buyingGuide && <CompareLink slug={buyingGuide.slug} name={guideShortName(buyingGuide)} className="flex lg:hidden" />}
            </div>
          )}
          {bodyTail && readNextInline && <ReadNextBox link={readNextInline} className={`my-10 ${readNextInlineClass}`} />}
          {bodyTail && !inlineOffer && shopLink && <ShopLinks link={shopLink} className="my-10" />}

          {bodyTail && (
            <div className="read-expert">
              {inlineCta && <CtaBanner genreSlug={article.genreSlug} slot="inline" className="my-10" />}
              <ArticleProse className="prose max-w-[40em]" html={bodyTail} slot={calcInline ? calcBox : undefined} priceSlot={priceBox} />
            </div>
          )}
          </div>

          {/* 計算機は合う章の下に置く。合う章が無い記事は本文の終わり（かんたん版でも見える位置） */}
          {calc && !calcInline && <CalculatorBox calc={calc} className="mt-12" />}

          {/* 今日やること（2〜3個）と、よくある質問（3〜5問）。合うジャンルの新しい記事だけ */}
          {todayActions.length >= 2 && <TodayActions actions={todayActions} />}
          {articleFaq.length >= 3 && <ArticleFaq items={articleFaq} />}

          {/* 読み終えたところでもう一度シェア */}
          <div className="mt-12 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-y border-line py-4">
            <p className="text-note font-bold text-ink">この記事をシェアする</p>
            <ShareButtons url={`${siteConfig.url}/articles/${article.slug}`} title={articleTitle(article)} />
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
                    ? { href: guideHref(guide.slug), label: `${guide.name}とは？できること・料金・使い方` }
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

          {/* 記事末の主導線（本文中に PR が無い記事だけ） */}
          {primaryCta && <CtaBanner genreSlug={article.genreSlug} slot="primary" className="mt-12" />}

          {/* 毎朝のメール配信と速報のプッシュ通知（また来てもらう導線） */}
          <NewsletterSignup variant="article" />
          <PushOptIn />

          {/* 本文が短く途中に置けなかった記事だけ、記事末に出す */}
          {!bodyTail && inlineOffer && (
            <div className="mt-12 max-w-[46em]">
              <RailRanking offer={inlineOffer} wide id="offer-inline" title={inlineOffer.headline} />
              {shopLink && <ShopLinks link={shopLink} className="mt-3" />}
              {buyingGuide && <CompareLink slug={buyingGuide.slug} name={guideShortName(buyingGuide)} className="flex lg:hidden" />}
            </div>
          )}
          {!bodyTail && readNextInline && <ReadNextBox link={readNextInline} className={`mt-12 ${readNextInlineClass}`} />}
          {!bodyTail && !inlineOffer && shopLink && <ShopLinks link={shopLink} className="mt-12" />}

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
            {/* 出典の種類（動画 / 公式発表・報道）に合わせた注記。動画の文言は動画から作った記事だけ */}
            {article.disclaimer ||
              (video
                ? "本記事は元動画の映像・音声をAIが解析して自動生成したものです。誤りが含まれる可能性があるため、正確な情報は公式発表をご確認ください。"
                : `本記事は${web && sourceName ? `${sourceName}の発表内容` : "公開されている情報"}をもとにAIが要約・解説して自動生成したものです。誤りが含まれる可能性があるため、正確な情報は${web ? "元記事" : "公式発表"}をご確認ください。`)}
            {sourceUrl && (
              <a
                href={sourceUrl}
                target="_blank"
                rel={web ? "noopener" : "noopener noreferrer"}
                className="mx-1 text-accent underline"
              >
                {video ? "元動画を見る" : "元記事を読む"}
              </a>
            )}
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
            <>
              {/* 合う広告が無い記事は、右側の先頭でガイドを案内する（スマホは本文中に出しているので重ねない） */}
              {readNextRail && <ReadNextBox link={readNextRail} className={`mb-8 ${readNextInline ? "hidden lg:block" : ""}`} />}
              <Sidebar current={article} showAds={showAds} />
            </>
          )}
        </aside>
      </div>

      {/* スマホ：読み進めると画面下にイチオシを出す。本文中に PR がある記事は出さない
          （スマホの PR は本文中1つ＋ページ下の1〜3位の最大2つ。画面下の帯まで重ねると3つになる） */}
      {offer && !hasInlinePr && <MobilePrBar item={offer.items[0]} watchId="offer-inline" />}
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

/** 表示時に経過時間（「3時間前」「昨日 13:55」）へ置き換わりうる新しさか。ページは最長1日キャッシュされるので3日まで見る */
function isRecent(iso: string): boolean {
  const time = Date.parse(iso);
  return Number.isFinite(time) && Date.now() - time < 3 * 86_400_000;
}

/** "12:34" / "1:02:03" → 秒 */
function toSeconds(time: string): number {
  const parts = time.split(":").map((p) => Number.parseInt(p, 10));
  if (parts.some(Number.isNaN)) return 0;
  return parts.reduce((total, part) => total * 60 + part, 0);
}
