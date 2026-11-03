import type { Metadata } from "next";
import { notFound } from "next/navigation";
import CompanyMark from "@/components/CompanyMark";
import CompanyChips from "@/components/CompanyChips";
import Pagination from "@/components/Pagination";
import Timeline from "@/components/Timeline";
import { getAllArticles } from "@/lib/articles";
import { COMPANY_PAGE_GENRE, companyCounts, companyOf, getCompany } from "@/lib/companies";
import { listingRobots } from "@/lib/indexing";
import { pageHref, pageSuffix, paginate } from "@/lib/paginate";
import { productsByCompany } from "@/lib/products";
import Link from "next/link";

export const aiArticles = () => getAllArticles().filter((a) => a.genreSlug === COMPANY_PAGE_GENRE);

/** 企業ページの metadata（1ページ目と2ページ目以降で共通） */
export function companyMetadata(slug: string, page: number): Metadata {
  const company = getCompany(slug);
  if (!company) return {};
  const items = aiArticles().filter((a) => companyOf(a).slug === slug);
  const count = items.length;
  const name = company.slug === "other" ? "その他のAI企業" : company.name;

  return {
    title: `${name}の最新ニュース・発表まとめ${pageSuffix(page)}`,
    description: `${name}の新モデル・新機能・料金などの最新発表を、日本語で要点だけ時系列にまとめています（${count}本）。`,
    alternates: { canonical: pageHref(`/companies/${slug}`, page) },
    ...listingRobots(items),
  };
}

/** 企業別の速報タイムライン。「OpenAI 最新」のような検索の受け皿にもなる */
export default function CompanyView({ slug, page }: { slug: string; page: number }) {
  const company = getCompany(slug);
  const all = aiArticles();
  const articles = all.filter((a) => companyOf(a).slug === slug);
  if (!company || articles.length === 0) notFound();
  const paged = paginate(articles, page, 100);
  if (page > paged.totalPages) notFound();

  const name = company.slug === "other" ? "その他のAI企業" : company.name;
  const products = company.slug === "other" ? [] : productsByCompany(company.slug);

  return (
    <>
      <CompanyChips companies={companyCounts(all).slice(0, 8)} active={slug} />
      <div className="mx-auto max-w-[920px] px-5 py-10 sm:px-8 sm:py-14">
        <div className="flex items-center gap-3">
          <CompanyMark company={company} size={40} />
          <p className="index-num text-meta font-semibold tracking-wider text-muted">
            {articles.length} 本の記事
          </p>
        </div>
        <h1 className="t-h1 mt-4">
          {name}の最新ニュース{pageSuffix(page)}
        </h1>
        <p className="t-lead mt-3">
          {name}に関する公式発表などをもとにした記事を、新しい順に並べています。各記事の「要点」だけ読めば、いま何が起きているかがつかめます。
        </p>
        {/* この企業の製品まとめ（「〇〇とは」を知りたい人の入口）。1ページ目だけ */}
        {page === 1 && products.length > 0 && (
          <section aria-labelledby="company-products" className="mt-8 border-y border-line py-5">
            <h2 id="company-products" className="t-h3">
              {name}の製品まとめ
            </h2>
            <ul className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {products.map((product) => (
                <li key={product.slug} className="text-[14.5px]">
                  <Link
                    href={`/products/${product.slug}`}
                    className="font-bold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                  >
                    {product.name}
                  </Link>
                  <span className="ml-2 text-meta text-muted">{product.category}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        <div className="mt-10">
          <Timeline articles={paged.items} filterable />
        </div>
        <Pagination base={`/companies/${slug}`} page={page} totalPages={paged.totalPages} />
      </div>
    </>
  );
}
