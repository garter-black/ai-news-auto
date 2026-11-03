import Link from "next/link";
import ArticleCard from "@/components/ArticleCard";
import { displayHeadline } from "@/lib/headline";
import { relatedLiveArticles, type RetiredEntry } from "@/lib/retired";

/**
 * 取り下げた記事の 410 ページの中身（2026-11-03 オーナー決定・台帳 W22）。
 * app/articles/[slug]/page.tsx が、記事が無く content/retired.json にある slug のときにこれを描く。
 * cloudflare/worker.ts は同じ URL を描かせて、状態コードだけ 410（もう無い）・noindex にして返す
 * （別の URL で描くと、ブラウザの URL と描いたページがずれて表示の後にエラーになるため）。
 * サイトのヘッダー・フッター・色はそのまま（app/layout.tsx）。説明は丁寧に短く、近い記事へのリンクを出す。
 */
export const GONE_TITLE = "この記事は掲載を終了しました";

const LINKS = [
  { href: "/", label: "トップページ" },
  { href: "/articles", label: "新着記事の一覧" },
  { href: "/editorial-policy", label: "編集方針" },
];

export default function GoneView({ entry }: { entry: RetiredEntry }) {
  const related = relatedLiveArticles(entry);

  return (
    <div className="mx-auto max-w-[1240px] px-4 py-14 sm:px-8 sm:py-20" data-gone-page>
      <div className="mx-auto max-w-2xl text-center">
        <p className="text-meta font-bold tracking-[0.08em] text-accent">掲載終了</p>
        <h1 className="mt-3 text-[1.5rem] font-extrabold leading-snug text-ink sm:text-[1.85rem]">{GONE_TITLE}</h1>
        {entry.title && (
          <p className="mt-4 text-note leading-[1.8] text-muted">
            「{displayHeadline(entry.title)}」
          </p>
        )}
        <p className="mt-3 text-[15px] leading-[1.9] text-ink-soft">
          サイソクニュースでは、読者の方に役立つ記事を中心にお届けするため、記事の整理を行っています。この記事はその中で掲載を終了しました。ご不便をおかけし、申し訳ありません。
        </p>
      </div>

      {related.length > 0 && (
        <section aria-labelledby="gone-related" className="mt-12">
          <h2 id="gone-related" className="t-h2 text-ink">
            こちらの記事もご覧ください
          </h2>
          <div className="mt-5 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {related.map((article) => (
              <ArticleCard key={article.slug} article={article} />
            ))}
          </div>
        </section>
      )}

      <ul className="mt-12 flex flex-wrap justify-center gap-x-6 gap-y-2 text-note">
        {LINKS.map((link) => (
          <li key={link.href}>
            <Link href={link.href} className="font-semibold text-accent underline underline-offset-4 hover:text-ink">
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
