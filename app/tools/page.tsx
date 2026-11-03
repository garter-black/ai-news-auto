import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import AssetShell, { Crumbs } from "@/components/assets/AssetShell";
import { CALCULATORS, type CalculatorKind } from "@/lib/calculators";

const DESCRIPTION = "ドル建てサブスクの円換算と、防衛特別所得税の計算。数字の出典と前提を書いた、すぐ使える小さな計算ツールです。";

export const metadata: Metadata = withPageShare("tools", "/tools", {
  title: "計算ツール｜サブスクの円換算・防衛特別所得税",
  description: DESCRIPTION,
  alternates: { canonical: "/tools" },
});

export default function ToolsPage() {
  return (
    <AssetShell>
      <Crumbs items={[{ href: "/", label: "ホーム" }, { label: "計算ツール" }]} />
      <header className="mt-5">
        <h1 className="t-h1 text-ink">計算ツール</h1>
        <p className="t-lead mt-3">ニュースを読んで「自分の場合はいくら？」と思ったときに。数字の出典と計算の前提を、それぞれのページに書いています。</p>
      </header>
      <ul className="mt-8 grid gap-3">
        {(Object.keys(CALCULATORS) as CalculatorKind[]).map((kind) => (
          <li key={kind}>
            <Link href={CALCULATORS[kind].href} className="group block rounded-2xl border border-line px-5 py-4 transition-colors hover:border-ink">
              <span className="t-card block text-ink group-hover:text-accent">{CALCULATORS[kind].title} →</span>
              <span className="mt-1 block text-desc leading-[1.75] text-ink-soft">{CALCULATORS[kind].description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </AssetShell>
  );
}
