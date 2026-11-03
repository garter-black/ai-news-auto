import { articleTitle } from "@/lib/headline";
import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import CompanyMark, { ArticleMark } from "@/components/CompanyMark";
import Timeline from "@/components/Timeline";
import VerdictBadge from "@/components/VerdictBadge";
import RailRanking from "@/components/rail/RailRanking";
import { getAllArticles, type ArticleSummary } from "@/lib/articles";
import { companyOf } from "@/lib/companies";
import { offerForTopic } from "@/lib/offers";
import { AI_TOPIC } from "@/lib/indexing";
import { isUnknownValue, usability, verdictKind, verdictOf } from "@/lib/verdict";
import { siteConfig } from "@/site.config";

const TITLE = "今すぐ使える最新AIツール・新機能まとめ【毎日更新】";
const DESCRIPTION =
  "発表されたばかりのAIの新機能・新モデルのうち、今日から使えるものだけを集めた一覧です。日本から使えるか・料金・無料で試せるかを、発表ごとに判定しています。";

export const metadata: Metadata = withPageShare("usable", "/usable", {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/usable" },
});

const dayFormat = new Intl.DateTimeFormat("ja-JP", { month: "numeric", day: "numeric", timeZone: "Asia/Tokyo" });

/**
 * 「使えるかどうか」の判定で絞り込んだハブページ。
 * 「最新 AI ツール」「今使える AI」のような検索と、AI検索からの引用の受け皿にする。
 * トップと同じく、左に時系列・右に補助の列を置いて横幅を使い切る。
 */
export default function UsablePage() {
  // AI の製品・サービスの記事だけ（ジャンルが「AI」でも AI でない話題・製品でない発表は出さない。2026-11-03 の点検・台帳 A27）
  const ai = getAllArticles().filter((a) => a.genreSlug === "ai" && AI_TOPIC.test(`${a.title} ${a.tags.join(" ")}`));
  const judged = ai.filter((a) => verdictOf(a) && verdictKind(a) === "product");
  const usable = judged.filter((a) => usability(verdictOf(a)!) === "go");
  const free = usable.filter((a) => verdictOf(a)?.free === true);
  const soon = judged.filter((a) => verdictOf(a)?.status === "soon");
  const updatedAt = usable[0]?.generatedAt;
  const offer = offerForTopic("business", "AIを仕事に取り入れるなら", 9);

  // 企業ごとの「今すぐ使える」本数
  const byCompany = new Map<string, { article: ArticleSummary; count: number }>();
  for (const article of usable) {
    const slug = companyOf(article).slug;
    const found = byCompany.get(slug);
    if (found) found.count += 1;
    else byCompany.set(slug, { article, count: 1 });
  }
  const companies = [...byCompany.values()].sort((a, b) => b.count - a.count).slice(0, 8);

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name: TITLE,
    description: DESCRIPTION,
    numberOfItems: Math.min(usable.length, 30),
    itemListElement: usable.slice(0, 30).map((article, index) => ({
      "@type": "ListItem",
      position: index + 1,
      url: `${siteConfig.url}/articles/${article.slug}`,
      name: articleTitle(article),
    })),
  };

  return (
    <div className="mx-auto max-w-[1240px] px-5 py-8 sm:px-8 sm:py-10">
      <script
        type="application/ld+json"
        suppressHydrationWarning
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* 見出しと数字を横に並べ、ファーストビューを詰める */}
      <header className="border-b border-ink pb-7">
        <div>
          <p className="flex items-center gap-2 text-meta font-bold text-muted">
            <VerdictBadge kind="go" />
            {updatedAt && <span className="index-num">{dayFormat.format(Date.parse(updatedAt))}更新</span>}
          </p>
          <h1 className="t-h1 mt-3">{TITLE}</h1>
          <p className="t-lead mt-3">{DESCRIPTION}</p>
        </div>
      </header>

      <div className="mt-10 grid gap-14 lg:grid-cols-[minmax(0,1fr)_320px] lg:gap-12">
        <div className="min-w-0">
          {/* 補助の一覧は左の列にまとめ、右側は商品枠だけにして最初から見えるようにする */}
          <div className="grid gap-10 sm:grid-cols-2">
            <RailList
              id="free-heading"
              title="無料で試せる"
              note="今日から使えて、無料プラン・無料枠があるもの"
              articles={free.slice(0, 5)}
            />
            <RailList id="soon-heading" title="もうすぐ使える" note="提供時期が発表されているもの" articles={soon.slice(0, 5)} />
          </div>

          {companies.length > 0 && (
            <section aria-labelledby="company-heading" className="mt-10">
              <RailHead id="company-heading" title="企業別：今すぐ使える数" />
              <ul className="flex flex-wrap gap-2">
                {companies.map(({ article, count }) => {
                  const company = companyOf(article);
                  return (
                    <li key={company.slug}>
                      <Link
                        href={`/companies/${company.slug}`}
                        className="flex items-center gap-2.5 rounded-md border border-line py-1.5 pl-2 pr-3 hover:border-ink"
                      >
                        <CompanyMark company={company} size={18} withName />
                        <span className="index-num text-meta font-bold text-go">{count}</span>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          <section aria-labelledby="usable-list" className="mt-12">
            <div className="mb-2 flex items-end justify-between gap-4">
              <h2 id="usable-list" className="t-h2">
                今日から使えるもの
              </h2>
              <span className="index-num pb-1 text-meta text-muted">新しい順・{Math.min(usable.length, 40)}本</span>
            </div>
            <Timeline articles={usable.slice(0, 40)} />
          </section>
        </div>

        {/* 開いた瞬間から右上に見え、読み進めても付いてくる商品枠 */}
        {offer && (
          <aside className="min-w-0">
            <div className="lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto lg:[scrollbar-width:thin]">
              <RailRanking offer={offer} />
            </div>
          </aside>
        )}
      </div>

      <p className="mt-12 rounded-2xl bg-surface px-5 py-4 text-note leading-relaxed text-muted">
        判定は各発表の時点の情報にもとづきます。提供地域・料金は変わることがあるため、最新の状況は各社の公式情報をご確認ください。
        <Link href="/about" className="ml-1 underline">
          サイソクについて
        </Link>
      </p>
    </div>
  );
}

function RailHead({ id, title, note }: { id: string; title: string; note?: string }) {
  return (
    <div className="mb-3 border-b border-ink pb-2">
      <h2 id={id} className="t-h3">
        {title}
      </h2>
      {note && <p className="mt-0.5 text-meta text-muted">{note}</p>}
    </div>
  );
}

function RailList({ id, title, note, articles }: { id: string; title: string; note?: string; articles: ArticleSummary[] }) {
  if (articles.length === 0) return null;
  return (
    <section aria-labelledby={id}>
      <RailHead id={id} title={title} note={note} />
      <ul className="grid gap-3.5">
        {articles.map((article) => {
          const verdict = verdictOf(article);
          return (
            <li key={article.slug}>
              <Link href={`/articles/${article.slug}`} className="group block">
                <span className="flex items-center gap-2 text-meta text-muted">
                  <ArticleMark company={companyOf(article)} genre={article.genre} size={14} />
                  {verdict?.price && !isUnknownValue(verdict.price) && <span>・{verdict.price}</span>}
                </span>
                <span className="mt-1 line-clamp-2 block text-[15px] font-bold leading-snug text-ink group-hover:underline">
                  {articleTitle(article)}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
