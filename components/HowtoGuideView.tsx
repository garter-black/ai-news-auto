import Link from "next/link";
import CompanyMark from "@/components/CompanyMark";
import Timeline from "@/components/Timeline";
import { formatDate } from "@/lib/articles";
import { getCompany, OTHER } from "@/lib/companies";
import { howtoArticles, siblingHowtos, type HowtoGuide } from "@/lib/howto";
import { getChooseGuide } from "@/lib/choose";
import { getProduct } from "@/lib/products";
import { siteConfig } from "@/site.config";
import { publisherJsonLd } from "@/lib/publisher";
import { difyDecisionHref } from "@/lib/a8-attribution";
import DifyCreditEstimator from "@/components/DifyCreditEstimator";
import WorkMoneyCard from "@/components/WorkMoneyCard";
import { workMoneyOf } from "@/lib/work-money";

/** 使い方ガイド（/guides/<slug>）。結論 → 手順 → 料金・無料の範囲 → 日本で使える？ → よくある質問 */
export default function HowtoGuideView({ guide }: { guide: HowtoGuide }) {
  const company = getCompany(guide.company) ?? OTHER;
  const news = howtoArticles(guide).slice(0, 8);
  const products = guide.products.map((slug) => getProduct(slug)).filter((p) => p !== undefined);
  const siblings = siblingHowtos(guide);
  const decisionGuide = guide.decisionGuideSlug ? getChooseGuide(guide.decisionGuideSlug) : undefined;
  const url = `${siteConfig.url}/guides/${guide.slug}`;
  const checked = formatDate(guide.checkedAt);
  const hasCreditEstimator = guide.slug === "dify-free-api-cost";
  const workMoney = workMoneyOf(guide.workMoney);

  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: guide.title,
      description: guide.description,
      inLanguage: "ja",
      datePublished: guide.createdAt,
      dateModified: guide.checkedAt > guide.updatedAt ? guide.checkedAt : guide.updatedAt,
      mainEntityOfPage: url,
      author: { "@type": "Organization", name: siteConfig.author, url: `${siteConfig.url}/about` },
      publisher: publisherJsonLd,
    },
    {
      "@context": "https://schema.org",
      "@type": "HowTo",
      name: guide.title,
      description: guide.description,
      inLanguage: "ja",
      step: guide.steps.map((step, index) => ({
        "@type": "HowToStep",
        position: index + 1,
        name: step.title,
        text: step.body,
        url: `${url}#step-${index + 1}`,
      })),
    },
    {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: guide.faq.map((item) => ({
        "@type": "Question",
        name: item.q,
        acceptedAnswer: { "@type": "Answer", text: item.a },
      })),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "ホーム", item: siteConfig.url },
        { "@type": "ListItem", position: 2, name: "使い方ガイド", item: `${siteConfig.url}/guides` },
        { "@type": "ListItem", position: 3, name: guide.title, item: url },
      ],
    },
  ];

  return (
    <div className="mx-auto max-w-[860px] px-5 py-8 sm:px-8 sm:py-12">
      <script
        type="application/ld+json"
        suppressHydrationWarning
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav aria-label="パンくず" className="flex flex-wrap items-center gap-1.5 text-meta text-muted">
        <Link href="/" className="hover:text-accent">ホーム</Link>
        <span aria-hidden>/</span>
        <Link href="/guides" className="hover:text-accent">使い方ガイド</Link>
        <span aria-hidden>/</span>
        <span>{guide.toolName}</span>
      </nav>

      <div className="mt-6 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-meta">
        {company.slug !== OTHER.slug && <CompanyMark company={company} size={20} withName />}
        <span className="text-muted">
          公式ページで確認 <span className="index-num font-semibold text-ink">{checked}</span>
        </span>
      </div>

      {guide.disclosure && (
        <p className="mt-3 border-l-4 border-ink bg-surface px-4 py-3 text-note leading-[1.8] text-ink" data-editorial-disclosure>
          <strong>PR</strong>　{guide.disclosure}
        </p>
      )}
      <h1 className="t-h1 headline mt-3">{guide.title}</h1>

      <section aria-labelledby="conclusion-heading" className="mt-6 rounded-2xl border-l-4 border-ink bg-surface p-5 sm:p-6">
        <h2 id="conclusion-heading" className="t-h3">結論</h2>
        <ul className="mt-4 grid gap-3">
          {guide.conclusion.map((point, index) => (
            <li key={index} className="grid grid-cols-[1rem_1fr] gap-2 text-desc font-medium leading-[1.75] text-ink">
              <span aria-hidden className="mt-[0.7em] size-1.5 rounded-full bg-ink" />
              {point}
            </li>
          ))}
        </ul>
      </section>

      {workMoney && <WorkMoneyCard workMoney={workMoney} />}

      <p className="mt-6 text-desc leading-[1.95] text-ink-soft">{guide.intro}</p>

      <nav aria-label="目次" className="mt-6 flex flex-wrap gap-x-4 gap-y-2 text-note font-bold text-ink-soft">
        {hasCreditEstimator && <a href="#credit-estimator" className="underline decoration-line-strong underline-offset-4 hover:text-accent">回数の目安を計算</a>}
        <a href="#steps" className="underline decoration-line-strong underline-offset-4 hover:text-accent">手順</a>
        <a href="#pricing" className="underline decoration-line-strong underline-offset-4 hover:text-accent">料金・無料でどこまで</a>
        <a href="#japan" className="underline decoration-line-strong underline-offset-4 hover:text-accent">日本で使える？</a>
        <a href="#faq" className="underline decoration-line-strong underline-offset-4 hover:text-accent">よくある質問</a>
      </nav>

      {hasCreditEstimator && <DifyCreditEstimator />}

      <Section id="steps" title="手順">
        <ol className="grid gap-6">
          {guide.steps.map((step, index) => (
            <li key={index} id={`step-${index + 1}`} className="grid scroll-mt-24 grid-cols-[2rem_1fr] items-start gap-3">
              <span className="index-num grid size-8 place-items-center rounded-lg border border-line-strong text-meta font-semibold text-ink">
                {index + 1}
              </span>
              <div className="min-w-0">
                <h3 className="t-card pt-1">{step.title}</h3>
                <p className="mt-1.5 text-desc leading-[1.9] text-ink-soft [overflow-wrap:anywhere]">{step.body}</p>
                {step.commands?.length ? (
                  <pre className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface p-4 text-[16px] leading-[1.8] text-ink" aria-label="状態を確認するコマンド">
                    <code>{step.commands.join("\n")}</code>
                  </pre>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section id="pricing" title="料金・無料でどこまで使える？">
        {guide.pricing.length > 0 && (
          <div className="overflow-x-auto rounded-2xl border border-line">
            <table className="w-full text-left text-note">
              <thead>
                <tr className="border-b border-line bg-surface">
                  <th scope="col" className="px-4 py-3 font-bold text-ink">プラン</th>
                  <th scope="col" className="px-4 py-3 font-bold text-ink">料金</th>
                  <th scope="col" className="px-4 py-3 font-bold text-ink">主な違い</th>
                </tr>
              </thead>
              <tbody>
                {guide.pricing.map((row) => (
                  <tr key={row.plan} className="border-b border-line last:border-0">
                    <th scope="row" className="px-4 py-3.5 align-top font-bold text-ink">{row.plan}</th>
                    <td className="whitespace-nowrap px-4 py-3.5 align-top font-bold text-ink">{row.price}</td>
                    <td className="px-4 py-3.5 align-top text-ink-soft">{row.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {guide.freeTier.length > 0 && (
          <>
            <h3 className="t-card mt-8">無料でできること・制限</h3>
            <ul className="mt-3 grid gap-2.5">
              {guide.freeTier.map((item, index) => (
                <li key={index} className="grid grid-cols-[1rem_1fr] gap-2 text-desc leading-[1.85] text-ink-soft">
                  <span aria-hidden className="mt-[0.75em] size-1.5 rounded-full bg-ink-soft" />
                  {item}
                </li>
              ))}
            </ul>
          </>
        )}
        <p className="mt-4 text-note leading-relaxed text-muted">
          {checked}時点で公式ページの表記を確認した内容です。料金や上限は変わることがあるため、申し込む前に公式サイトで最新の情報を確認してください。
        </p>
        {guide.officialAction && (
          <a href={guide.officialAction.url} target="_blank" rel="sponsored nofollow noopener noreferrer" className="mt-5 inline-flex rounded-lg border border-ink px-5 py-3 text-note font-bold text-ink underline underline-offset-4 hover:text-accent">
            {guide.officialAction.label}
          </a>
        )}
      </Section>

      <Section id="japan" title="日本で使える？">
        <p className="text-[17px] font-bold leading-[1.7] text-ink">{guide.japan.answer}</p>
        <p className="mt-2 text-desc leading-[1.9] text-ink-soft">{guide.japan.body}</p>
      </Section>

      {guide.tips.length > 0 && (
        <Section id="tips" title="つまずきやすい点と注意">
          <div className="grid gap-3 sm:grid-cols-2">
            {guide.tips.map((tip) => (
              <div key={tip.title} className="rounded-2xl border border-line p-5 sm:[&:last-child:nth-child(odd)]:col-span-2">
                <h3 className="t-card">{tip.title}</h3>
                <p className="mt-2 text-desc leading-[1.85] text-ink-soft">{tip.body}</p>
              </div>
            ))}
          </div>
        </Section>
      )}

      {decisionGuide ? (
        <aside aria-label="関連する選び方ガイド" data-aff-pos="howto-decision" data-funnel-pos="howto-decision" className="mt-10 border-l-[3px] border-accent bg-surface px-4 py-4">
          <Link href={difyDecisionHref(`/guides/${guide.slug}`, `/choose/${decisionGuide.slug}`)} className="text-[17px] font-bold leading-relaxed text-ink underline decoration-line-strong underline-offset-4 hover:text-accent">
            {decisionGuide.articleLabel}
          </Link>
          <p className="mt-2 text-desc leading-[1.85] text-ink-soft">{decisionGuide.articleNote}</p>
        </aside>
      ) : null}

      <Section id="faq" title="よくある質問">
        <div className="divide-y divide-line border-y border-line">
          {guide.faq.map((item) => (
            <details key={item.q} className="group py-4" open>
              <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-[15.5px] font-bold text-ink">
                <span className="min-w-0 [overflow-wrap:anywhere]">Q. {item.q}</span>
                <span aria-hidden className="mt-1 text-muted transition-transform group-open:rotate-45">＋</span>
              </summary>
              <p className="mt-3 text-desc leading-[1.9] text-ink-soft">A. {item.a}</p>
            </details>
          ))}
        </div>
      </Section>

      {news.length > 0 && (
        <Section id="news" title={`${guide.toolName}の最新ニュース`}>
          <Timeline articles={news} />
        </Section>
      )}

      {(products.length > 0 || siblings.length > 0) && (
        <Section id="more" title="あわせて読む">
          <ul className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {products.map((product) => (
              <li key={product.slug} className="text-[14.5px] leading-snug">
                <Link
                  href={`/products/${product.slug}`}
                  className="font-bold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                >
                  {product.name}とは？（まとめ）
                </Link>
              </li>
            ))}
            {siblings.map((other) => (
              <li key={other.slug} className="text-[14.5px] leading-snug">
                <Link
                  href={`/guides/${other.slug}`}
                  className="font-bold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent hover:decoration-accent"
                >
                  {other.disclosure && <span className="mr-1">PR</span>}{other.title}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <section aria-labelledby="sources-heading" className="mt-14 rounded-2xl bg-surface px-5 py-5">
        <p id="sources-heading" className="text-note font-bold text-ink">参考にした公式情報（確認日 {checked}）</p>
        <ul className="mt-3 grid gap-2 text-note leading-relaxed">
          {guide.sources.map((source) => (
            <li key={source.url} className="min-w-0 break-words">
              <a href={source.url} target="_blank" rel="noopener noreferrer" className="text-ink-soft underline underline-offset-4 hover:text-accent">
                {source.title || source.url}
              </a>
            </li>
          ))}
        </ul>
        <p className="mt-4 text-note leading-relaxed text-muted">
          {guide.editorialNote ?? "このページは、上の公式ページの内容だけをもとに編集部が作成し、別のAIで公式ページと照合しています。公式ページは14日ごと、料金などの関連ニュースが出たときはその都度確認し直しています。"}
          <Link href="/editorial-policy" className="ml-1 text-accent underline">編集方針</Link>
        </p>
      </section>
    </div>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={`${id}-heading`} id={id} className="mt-14 scroll-mt-24">
      <div className="mb-5 border-b border-ink pb-2">
        <h2 id={`${id}-heading`} className="t-h2">
          {title}
        </h2>
      </div>
      {children}
    </section>
  );
}
