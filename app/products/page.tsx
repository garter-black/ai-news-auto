import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import CompanyMark from "@/components/CompanyMark";
import ProductVisual, { BrandImg } from "@/components/ProductVisual";
import { getCompany } from "@/lib/companies";
import { listedProducts, productProfile, vendorLogo, type ProductProfile } from "@/lib/products";

export const metadata: Metadata = withPageShare("products", "/products", {
  title: "AIサービス図鑑：料金・日本での提供状況・最新の動き",
  description:
    "ChatGPT・Codex・GPT-6・Geminiなど主なAIサービスを、公式の料金ページと日本での提供状況、サイソクの記事にある発表の日付から製品ごとに整理。料金と提供状況の確認日を明記しています。",
  alternates: { canonical: "/products" },
});

/** カードの説明（冒頭の2文。何の製品で、何ができるか） */
function firstSentence(text: string): string {
  return (text.match(/^[^。]+。(?:[^。]+。)?/)?.[0] ?? text).trim();
}

/** AIサービス図鑑の一覧（中身がそろった製品だけ。提供元ごと・関連度の高い順） */
export default function ProductsPage() {
  const profiles = listedProducts().map(productProfile);
  const groups = new Map<string, ProductProfile[]>();
  for (const p of profiles) {
    const key = p.product.company === "other" ? `other:${p.product.companyName}` : p.product.company;
    groups.set(key, [...(groups.get(key) ?? []), p]);
  }
  // 提供元の並びも、いちばん関連度の高い製品の順
  const keys = [...groups.keys()].sort((a, b) => groups.get(b)![0].relevance - groups.get(a)![0].relevance);
  const checked = profiles.map((p) => p.pricingTable?.checkedAt ?? "").filter(Boolean).sort().pop();

  return (
    <div className="mx-auto max-w-[860px] px-4 py-10 sm:px-8 sm:py-14">
      <h1 className="t-h1">AIサービス図鑑</h1>
      <p className="t-lead mt-3">
        主なAIサービスを製品ごとに整理しています。料金は各社の公式料金ページ、日本での提供状況は公式の対応国・地域の案内で確認し、発表や更新の日付はサイソクの記事から載せています。
      </p>
      <p className="mt-3 text-note leading-[1.8] text-ink-soft">
        掲載は{profiles.length}製品{checked ? `（料金の確認日 ${checked}）` : ""}。公式の料金や提供状況を確かめられていない製品は、確認できるまで掲載していません。
      </p>

      <div className="mt-10 grid gap-12">
        {keys.map((key) => {
          const items = groups.get(key)!;
          const first = items[0].product;
          const company = first.company === "other" ? undefined : getCompany(first.company);
          const logo = vendorLogo(first.company);
          return (
            <section key={key} aria-label={first.companyName}>
              <h2 className="t-h2 flex items-center gap-2 border-b-2 border-ink pb-2.5">
                {logo ? (
                  // 公式のワードマーク（社名入り）。ロゴのまわりは公式素材の余白のまま
                  <span className="inline-flex h-9 items-center">
                    <BrandImg image={logo} alt={logo.alt ?? first.companyName} width={Math.round(36 * ((logo.width ?? 360) / (logo.height ?? 161)))} height={36} className="h-9 w-auto" />
                  </span>
                ) : company ? (
                  <CompanyMark company={company} size={26} withName />
                ) : (
                  first.companyName
                )}
                <span className="index-num ml-auto text-meta font-normal text-muted">{items.length}製品</span>
              </h2>
              <ul>
                {items.map((p) => (
                  <li key={p.product.slug} className="border-b border-line">
                    <Link href={`/products/${p.product.slug}`} className="group grid grid-cols-[48px_minmax(0,1fr)] gap-4 py-5 sm:grid-cols-[56px_minmax(0,1fr)]">
                      <ProductVisual company={p.product.company} image={p.source?.image} size={56} className="size-12 sm:size-14" />
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                          <span className="t-card group-hover:underline">{p.product.name}</span>
                          <span className="text-meta text-muted">{p.product.category}</span>
                        </span>
                        <span className="mt-1.5 block text-desc leading-[1.8] text-ink-soft">{firstSentence(p.overview)}</span>
                        <dl className="mt-2.5 grid gap-x-6 gap-y-1 text-note sm:grid-cols-3">
                          {p.startingPrice && (
                            <div className="flex gap-2">
                              <dt className="shrink-0 text-muted">料金</dt>
                              <dd className="font-bold text-ink">{p.startingPrice}{p.startingPriceNote ? <span className="ml-1 font-normal text-muted">{p.startingPriceNote}</span> : null}</dd>
                            </div>
                          )}
                          {p.japan && (
                            <div className="flex gap-2">
                              <dt className="shrink-0 text-muted">日本</dt>
                              <dd className="font-bold text-ink">{p.japan.label}</dd>
                            </div>
                          )}
                          <div className="flex gap-2">
                            <dt className="shrink-0 text-muted">更新</dt>
                            <dd className="index-num text-ink">{p.updatedAt.slice(0, 10).replaceAll("-", "/")}</dd>
                          </div>
                        </dl>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      {(() => {
        // 画像の出典（同じ出典は1回だけ）
        const credits = new Map<string, string>();
        for (const p of profiles) {
          const images = [p.source?.image, vendorLogo(p.product.company)].filter((i): i is NonNullable<typeof i> => Boolean(i));
          for (const image of images) credits.set(image.sourceUrl, image.credit);
        }
        if (credits.size === 0) return null;
        return (
          <p className="mt-10 text-meta leading-[1.8] text-muted">
            {[...credits].map(([url, credit]) => (
              <span key={url} className="mr-4 inline-block">
                <a href={url} className="underline underline-offset-2 hover:text-accent" target="_blank" rel="noopener noreferrer">
                  {credit}
                </a>
              </span>
            ))}
          </p>
        );
      })()}

      <p className="mt-12 border-t border-line pt-5 text-note leading-[1.9] text-muted">
        料金・提供条件は変わることがあります。各製品のページに公式の出典と確認日を載せています。AIの料金を横に並べて比べるなら
        <Link href="/ai-pricing" className="mx-1 font-bold text-ink underline underline-offset-4">AIの料金・日本対応 早見表</Link>
        をご覧ください。
      </p>
    </div>
  );
}
