import { getAllChooseGuides } from "@/lib/choose";
import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import CompanyMark from "@/components/CompanyMark";
import { formatDate } from "@/lib/articles";
import { getCompany, OTHER } from "@/lib/companies";
import { getAllGuides, guideArticles } from "@/lib/guides";
import { getAllHowtos, type HowtoGuide } from "@/lib/howto";
import { supersedesGuide } from "@/lib/products";

export const metadata: Metadata = withPageShare("guides", "/guides", {
  title: "AIの使い方ガイド：始め方・料金・無料でどこまで使えるか",
  description:
    "ChatGPT・Gemini・Claude・NotebookLMなどAIツールの使い方と、AIを使う仕事・サイト運営を解説。料金、無料で使える範囲、導入条件を公式情報と確認日付きで整理しています。",
  alternates: { canonical: "/guides" },
});

/** 使い方ガイドと製品ガイドの一覧 */
export default function GuidesPage() {
  const howtos = getAllHowtos();
  // まとめページ（/products）へ移した製品ガイドは転送しているので出さない
  const guides = getAllGuides().filter((guide) => !supersedesGuide(guide.slug));
  const groups = new Map<string, HowtoGuide[]>();
  for (const guide of howtos) {
    const key = guide.toolName.split(" / ")[0];
    groups.set(key, [...(groups.get(key) ?? []), guide]);
  }

  return (
    <div className="mx-auto max-w-[920px] px-5 py-10 sm:px-8 sm:py-14">
      <p className="eyebrow">GUIDES</p>
      <h1 className="t-h1 mt-2">AIの使い方ガイド</h1>
      <p className="t-lead mt-3">
        AIツールの使い方と、AIを使う仕事・サイト運営をまとめました。公式情報と確認日を示し、提供条件と編集部の判断を分けて整理しています。
      </p>

      <section aria-labelledby="choose-guides" className="mt-12" data-funnel-pos="guide-list">
        <h2 id="choose-guides" className="t-h2 border-b border-ink pb-2">導入する前に、費用と条件を比べる</h2>
        <ul className="mt-5 grid gap-3 sm:grid-cols-2">
          {getAllChooseGuides().map((guide) => (
            <li key={guide.slug}><Link href={`/choose/${guide.slug}`} className="group flex h-full flex-col gap-3 rounded-2xl border border-line p-5 transition-colors hover:border-ink">
              <span className="text-[17px] font-bold leading-snug text-ink group-hover:underline">{guide.articleLabel}</span>
              <span className="text-desc leading-[1.75] text-ink-soft">{guide.articleNote}</span>
            </Link></li>
          ))}
        </ul>
      </section>

      {[...groups.entries()].map(([tool, items]) => (
        <section key={tool} aria-labelledby={`tool-${items[0].slug}`} className="mt-12">
          <h2 id={`tool-${items[0].slug}`} className="t-h2 border-b border-ink pb-2">{tool}</h2>
          <ul className="mt-5 grid gap-3 sm:grid-cols-2">
            {items.map((guide) => {
              const company = getCompany(guide.company) ?? OTHER;
              return (
                <li key={guide.slug}>
                  <Link
                    href={`/guides/${guide.slug}`}
                    className="group flex h-full flex-col gap-3 rounded-2xl border border-line p-5 transition-colors hover:border-ink"
                  >
                    <span className="flex items-center gap-2 text-meta">
                      {guide.disclosure && <span className="font-bold text-ink">PR</span>}
                      {company.slug !== OTHER.slug ? (
                        <CompanyMark company={company} size={18} withName />
                      ) : (
                        <span className="font-bold text-ink-soft">{guide.intent}</span>
                      )}
                      <span className="index-num ml-auto text-muted">確認 {formatDate(guide.checkedAt)}</span>
                    </span>
                    <span className="text-[17px] font-bold leading-snug text-ink group-hover:underline">{guide.title}</span>
                    <span className="line-clamp-2 text-desc leading-[1.75] text-ink-soft">{guide.conclusion[0]}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      ))}

      {guides.length > 0 && (
        <section aria-labelledby="product-guides" className="mt-12">
          <h2 id="product-guides" className="t-h2 border-b border-ink pb-2">製品ガイド</h2>
          <ul className="mt-5 grid gap-3 sm:grid-cols-2">
            {guides.map((guide) => {
              const company = getCompany(guide.companySlug) ?? OTHER;
              return (
                <li key={guide.slug}>
                  <Link
                    href={`/guides/${guide.slug}`}
                    className="group flex h-full flex-col gap-3 rounded-2xl border border-line p-5 transition-colors hover:border-ink"
                  >
                    <span className="flex items-center gap-2 text-meta">
                      {company.slug !== OTHER.slug && <CompanyMark company={company} size={18} withName />}
                      <span className="index-num ml-auto text-muted">関連 {guideArticles(guide).length}本</span>
                    </span>
                    <span className="text-[17px] font-bold leading-snug text-ink group-hover:underline">{guide.title}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <p className="mt-14 text-note leading-relaxed text-muted">
        新しいAIの「とは・料金・日本で使える？」は
        <Link href="/products" className="mx-1 font-bold text-ink underline underline-offset-4">製品のまとめページ</Link>
        、各社の料金の一覧は
        <Link href="/ai-pricing" className="mx-1 font-bold text-ink underline underline-offset-4">AIの料金・日本対応 早見表</Link>
        にまとめています。
      </p>
    </div>
  );
}
