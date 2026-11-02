import Link from "next/link";
import type { ReactNode } from "react";
import AssetShell, { Crumbs } from "@/components/assets/AssetShell";
import { Calculator, CALC_NOTES } from "@/components/calc/CalculatorBox";
import { CALCULATORS, type CalculatorKind } from "@/lib/calculators";
import { siteConfig } from "@/site.config";

/** 計算機の単独ページ（/tools/*）の共通の枠 */
export default function ToolPage({ kind, lead, children }: { kind: CalculatorKind; lead: string; children?: ReactNode }) {
  const meta = CALCULATORS[kind];
  const url = `${siteConfig.url}${meta.href}`;
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "WebApplication",
      name: meta.title,
      description: meta.description,
      url,
      applicationCategory: "FinanceApplication",
      operatingSystem: "Web",
      inLanguage: "ja",
      isAccessibleForFree: true,
      offers: { "@type": "Offer", price: 0, priceCurrency: "JPY" },
      publisher: { "@type": "Organization", name: siteConfig.siteName, url: siteConfig.url },
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: "計算ツール", item: `${siteConfig.url}/tools` },
        { "@type": "ListItem", position: 3, name: meta.title, item: url },
      ],
    },
  ];
  return (
    <AssetShell>
      <script type="application/ld+json" suppressHydrationWarning dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }} />
      <Crumbs items={[{ href: "/", label: "ホーム" }, { href: "/tools", label: "計算ツール" }, { label: meta.title }]} />
      <header className="mt-5">
        <h1 className="t-h1 text-ink">{meta.title}</h1>
        <p className="t-lead mt-3">{lead}</p>
      </header>
      <section aria-label="計算" className="mt-7 rounded-2xl border border-line-strong p-5 sm:p-6">
        <Calculator calc={{ kind }} />
      </section>
      <p className="mt-4 max-w-[40em] text-note leading-[1.75] text-ink-soft">{CALC_NOTES[kind]}</p>
      <p className="mt-2 max-w-[40em] text-note leading-[1.75] text-ink-soft">入力した数字は、この画面の中の計算だけに使います。送信・保存はしません。</p>
      {children ? <div className="mt-12 max-w-[40em]">{children}</div> : null}
      <nav aria-label="ほかの計算ツール" className="mt-12 border-t border-line pt-6">
        <p className="text-meta font-bold text-muted">ほかの計算ツール</p>
        <ul className="mt-2 grid gap-1.5">
          {(Object.keys(CALCULATORS) as CalculatorKind[])
            .filter((k) => k !== kind)
            .map((k) => (
              <li key={k}>
                <Link href={CALCULATORS[k].href} className="text-desc font-bold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent">
                  {CALCULATORS[k].title}
                </Link>
              </li>
            ))}
        </ul>
      </nav>
    </AssetShell>
  );
}

/** 単独ページの説明の章 */
export function ToolSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-10 first:mt-0">
      <h2 className="t-h2 border-b border-line-strong pb-2.5">{title}</h2>
      <div className="mt-4 grid gap-3 text-desc leading-[1.85] text-ink-soft">{children}</div>
    </section>
  );
}
