import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { laterPages, parsePage } from "@/lib/paginate";
import { companyCounts, companyOf } from "@/lib/companies";
import CompanyView, { aiArticles, companyMetadata } from "../../view";

type Props = { params: Promise<{ company: string; n: string }> };

// 2ページ目以降もビルド時に静的化する（デプロイ直後の初回アクセスで作らない。台帳 W29）
export const dynamicParams = true;
export const revalidate = 86400;

export function generateStaticParams() {
  const all = aiArticles();
  return companyCounts(all).flatMap(({ company }) =>
    laterPages(all.filter((a) => companyOf(a).slug === company.slug).length, 100).map((n) => ({ company: company.slug, n })),
  );
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { company, n } = await params;
  const page = parsePage(n);
  return page ? companyMetadata(company, page) : {};
}

export default async function CompanyPagedPage({ params }: Props) {
  const { company, n } = await params;
  const page = parsePage(n);
  if (!page) notFound();
  return <CompanyView slug={company} page={page} />;
}
