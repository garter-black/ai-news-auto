import { COMPARE_PUBLIC } from "@/lib/buying-guides";
import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import { formatDate } from "@/lib/articles";
import { getAllBuyingGuides, guideShortName, resolveProducts } from "@/lib/buying-guides";
import { PrMark, ProductVisual } from "@/components/compare/parts";

export const metadata: Metadata = withPageShare("compare", "/compare", {
  title: "比較ガイド：AI時代の道具を公式情報で比べる",
  description:
    "セキュリティソフト、Dify・n8n用のVPS、AIボイスレコーダー、ローカルLLM用PC、動画編集ソフト、AI英会話などを、公式情報と価格をもとに比較。選び方とよくある質問もまとめています。",
  alternates: { canonical: "/compare" },
  ...(COMPARE_PUBLIC ? {} : { robots: { index: false, follow: true } }),
});

/** 比較ガイドの一覧 */
export default function CompareIndexPage() {
  const guides = getAllBuyingGuides();
  return (
    <div className="mx-auto max-w-[920px] px-5 py-10 sm:px-8 sm:py-14">
      <p className="eyebrow">COMPARE</p>
      <h1 className="t-h1 mt-2">比較ガイド</h1>
      <p className="t-lead mt-3">
        AIを使うための道具やサービスを、各社の公式情報と価格をもとに比べました。価格は定期的に確認し直しています。
      </p>
      <p className="mt-3 text-note text-muted">
        本ページはプロモーションを含みます（「PR」の付いたガイドに提携先の商品があります）。
      </p>
      <ul className="mt-10 grid gap-4 sm:grid-cols-2">
        {guides.map((guide) => {
          const products = resolveProducts(guide);
          const shown = products.slice(0, 4);
          const rest = products.length - shown.length;
          const hasPr = products.some((p) => p.sponsored);
          return (
            <li key={guide.slug}>
              <Link
                href={`/compare/${guide.slug}`}
                className="group flex h-full flex-col overflow-hidden rounded-2xl border border-line transition-colors hover:border-ink"
              >
                {/* 比べている商品の写真（提携外のサービスは名前の札） */}
                <span className="grid grid-cols-4 gap-px border-b border-line bg-line" aria-hidden>
                  {shown.map((product) => (
                    <ProductVisual key={product.key} product={product} compact className="aspect-square w-full border-0" />
                  ))}
                  {Array.from({ length: 4 - shown.length }, (_, i) => (
                    <span key={`blank-${i}`} className="aspect-square bg-surface" />
                  ))}
                </span>
                <span className="flex flex-1 flex-col gap-2 p-4">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted">
                    <span className="index-num font-semibold text-ink">{products.length}件を比較</span>
                    <span aria-hidden>・</span>
                    <span className="index-num">{formatDate(guide.updatedAt)} 更新</span>
                    {hasPr && <PrMark className="ml-auto" />}
                  </span>
                  <span className="text-[16.5px] font-bold leading-snug text-ink group-hover:underline">{guideShortName(guide)}</span>
                  <span className="line-clamp-2 text-desc leading-[1.7] text-ink-soft">
                    {products.map((p) => p.item.name).join(" / ")}
                    {rest > 0 && ` ほか`}
                  </span>
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
