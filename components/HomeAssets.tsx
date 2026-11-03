import Link from "next/link";
import { getAllArticles } from "@/lib/articles";
import { getAllChooseGuides } from "@/lib/choose";
import { getAllHowtos } from "@/lib/howto";
import { getPricing } from "@/lib/pricing";

/** トップの「サイソクで調べられること」。独自にまとめている定番ページを控えめに並べる */
export default function HomeAssets() {
  const howtos = getAllHowtos().length;
  const chooses = getAllChooseGuides().length;
  const judged = getAllArticles().filter((a) => a.verdict).length;
  const pricingAt = getPricing().updatedAt?.slice(0, 10).replace(/-/g, "/") ?? "";

  const items = [
    { href: "/usable", title: "日本で使える？無料？", note: `発表ごとに判定（${judged}本）` },
    { href: "/guides", title: "使い方ガイド", note: `料金・無料枠・始め方（${howtos}本）` },
    { href: "/ai-pricing", title: "AIの料金早見表", note: pricingAt ? `公式ページで確認 ${pricingAt}` : "主要AIの料金と日本対応" },
    { href: "/choose/internet-for-ai", title: "選び方ガイド", note: `回線・VPS・転職など（${chooses}本）` },
  ];

  return (
    <section aria-labelledby="assets-heading" className="mt-10">
      <h2 id="assets-heading" className="t-h2">
        サイソクで調べられること
      </h2>
      <ul className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        {items.map((item) => (
          <li key={item.href}>
            <Link
              href={item.href}
              className="block h-full rounded-xl border border-line px-4 py-3 transition-colors hover:border-ink"
            >
              <span className="block text-note font-bold text-ink">{item.title}</span>
              <span className="mt-0.5 block text-meta leading-snug text-muted">{item.note}</span>
            </Link>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-note text-muted">
        記事は企業・官公庁の公式発表などの一次情報をもとに作り、出典と自動照合してから公開しています。
        <Link href="/editorial-policy" className="ml-1 underline underline-offset-4 hover:text-ink">
          制作と確認の方法
        </Link>
        ・
        <Link href="/corrections" className="underline underline-offset-4 hover:text-ink">
          訂正履歴
        </Link>
      </p>
    </section>
  );
}
