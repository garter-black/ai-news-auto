import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import CompanyMark from "@/components/CompanyMark";
import Thumb from "@/components/Thumb";
import { coverPath, formatDate, getArticleSummary } from "@/lib/articles";
import { COMPANIES, getCompany, thumbSource } from "@/lib/companies";
import { getAllProducts, type Product } from "@/lib/products";

export const metadata: Metadata = withPageShare("products", "/products", {
  title: "AIサービス図鑑：できること・料金・最新ニュース",
  description:
    "GPT-6.1 Terra、Claude Opus 5.5、Gemini 4 Argonなど、記事に出てきたAIモデル・アプリ・ガジェットを製品ごとに1ページで整理。料金・日本で使えるか・これまでの流れを、記事が増えるたびに更新します。",
  alternates: { canonical: "/products" },
});

/** 製品まとめの一覧（企業ごと） */
export default function ProductsPage() {
  const products = getAllProducts();
  // lib/companies.ts の並び（記事の多い企業から）→ その他の企業は名前順
  const known = COMPANIES.map((c) => c.slug);
  const groups = new Map<string, Product[]>();
  for (const product of products) {
    const key = product.company === "other" ? `other:${product.companyName}` : product.company;
    groups.set(key, [...(groups.get(key) ?? []), product]);
  }
  const keys = [...groups.keys()].sort((a, b) => {
    const ia = known.indexOf(a);
    const ib = known.indexOf(b);
    if (ia >= 0 && ib >= 0) return groups.get(b)!.length - groups.get(a)!.length || ia - ib;
    if (ia >= 0) return -1;
    if (ib >= 0) return 1;
    return a.localeCompare(b);
  });

  return (
    <div className="mx-auto max-w-[860px] px-4 py-10 sm:px-8 sm:py-14">
      <h1 className="t-h1">AIサービス図鑑</h1>
      <p className="t-lead mt-3">
        記事に出てきたAIモデル・アプリ・ガジェットを、製品ごとに1ページにまとめています。できることや提供状況、関連記事を調べられます。料金や利用条件は必ず公式情報もご確認ください。
      </p>

      <div className="mt-10 grid gap-12">
        {keys.map((key) => {
          const items = groups.get(key)!;
          const company = items[0].company === "other" ? undefined : getCompany(items[0].company);
          return (
            <section key={key} aria-label={items[0].companyName}>
              <h2 className="t-h2 flex items-center gap-2 border-b-2 border-ink pb-2.5">
                {company ? <CompanyMark company={company} size={26} withName /> : items[0].companyName}
                <span className="index-num ml-auto text-meta font-normal text-muted">{items.length}製品</span>
              </h2>
              <ul>
                {items.map((product) => {
                  const article = product.articles.map(getArticleSummary).find(Boolean);
                  return (
                  <li key={product.slug} className="border-b border-line">
                    <Link href={`/products/${product.slug}`} className="group grid grid-cols-[96px_minmax(0,1fr)] gap-4 py-4 sm:grid-cols-[160px_minmax(0,1fr)]">
                      <span className="relative block aspect-[40/21] overflow-hidden rounded-lg bg-surface-2">
                        {article && <Thumb src={article.thumbnail || coverPath(article) || ""} source={thumbSource(article)} alt="" fill sizes="(max-width: 640px) 96px, 160px" className="object-contain" />}
                      </span>
                      <span className="min-w-0">
                      <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <span className="t-card group-hover:underline">{product.name}</span>
                        <span className="text-meta text-muted">{product.category}</span>
                        <span className="index-num ml-auto text-meta text-muted">
                          記事{product.articles.length}本・{formatDate(product.updatedAt)}更新
                        </span>
                      </span>
                      <span className="mt-1.5 line-clamp-2 block text-desc leading-[1.8] text-ink-soft">
                        {product.overview.text}
                      </span>
                      </span>
                    </Link>
                  </li>
                );})}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
