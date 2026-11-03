import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ChooseView from "@/components/choose/ChooseView";
import { getAllArticles } from "@/lib/articles";
import { getAllHowtos } from "@/lib/howto";
import { chooseGuideForArticle, getAllChooseGuides, getChooseGuide, resolveService } from "@/lib/choose";
import { siteConfig } from "@/site.config";
import { authorJsonLd, publisherJsonLd } from "@/lib/publisher";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return getAllChooseGuides().map((guide) => ({ slug: guide.slug }));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const guide = getChooseGuide(slug);
  if (!guide) return {};
  const url = `/choose/${guide.slug}`;
  return {
    title: guide.title,
    description: guide.description,
    keywords: guide.keywords,
    alternates: { canonical: url },
    openGraph: { siteName: siteConfig.siteName, locale: siteConfig.locale,
      type: "article",
      url,
      title: guide.h1,
      description: guide.description,
      publishedTime: guide.publishedAt,
      modifiedTime: guide.updatedAt,
    },
    twitter: { card: "summary_large_image", title: guide.h1, description: guide.description },
  };
}

export default async function ChoosePage({ params }: Props) {
  const { slug } = await params;
  const guide = getChooseGuide(slug);
  if (!guide) notFound();

  const services = guide.services.map(resolveService);
  // このガイドに合う最近のニュース（記事からガイドへの案内と同じ条件）
  const related = getAllArticles()
    .filter((article) => chooseGuideForArticle(article)?.slug === guide.slug)
    .slice(0, 4);
  const others = [
    ...getAllHowtos()
      .filter((howto) => howto.decisionGuideSlug === guide.slug)
      .map((howto) => ({ href: `/guides/${howto.slug}`, title: howto.title, note: howto.description })),
    ...getAllChooseGuides()
      .filter((g) => g.slug !== guide.slug)
      .map((g) => ({ href: `/choose/${g.slug}`, title: g.articleLabel, note: g.articleNote })),
    { href: "/ai-pricing", title: "AIの料金・日本対応 早見表", note: "ChatGPT・Claude・Geminiの料金を1ページで" },
  ];

  const url = `${siteConfig.url}/choose/${guide.slug}`;
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: guide.h1,
      description: guide.description,
      inLanguage: "ja",
      datePublished: guide.publishedAt,
      dateModified: guide.updatedAt,
      mainEntityOfPage: url,
      author: authorJsonLd,
      publisher: publisherJsonLd,
      publishingPrinciples: `${siteConfig.url}/editorial-policy`,
      keywords: guide.keywords.join(", "),
    },
    {
      "@context": "https://schema.org",
      "@type": "ItemList",
      name: `${guide.h1}：比較したサービス`,
      numberOfItems: services.length,
      itemListElement: services.map((service, index) => ({
        "@type": "ListItem",
        position: index + 1,
        name: service.name,
        url: service.officialUrl,
      })),
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: guide.faq.map((item) => ({ "@type": "Question", name: item.q, acceptedAnswer: { "@type": "Answer", text: item.a } })),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: guide.h1, item: url },
      ],
    },
  ];

  return <ChooseView guide={guide} services={services} related={related} others={others} jsonLd={jsonLd} />;
}
