import Link from "next/link";
import AdSlot from "@/components/AdSlot";
import AffiliateBox from "@/components/AffiliateBox";
import CtaBanner from "@/components/CtaBanner";
import {
  formatDate,
  getAllArticles,
  getAllTags,
  type AffiliateLink,
  type Article,
} from "@/lib/articles";
import { displayHeadline } from "@/lib/headline";

type Props = {
  current?: Article;
  affiliates?: AffiliateLink[];
  /** false のとき広告枠を出さない（検索に出さない記事。app/articles/[slug]/page.tsx） */
  showAds?: boolean;
};

export default function Sidebar({ current, affiliates = [], showAds = true }: Props) {
  const latest = getAllArticles()
    .filter((a) => a.genreSlug === "ai" && a.slug !== current?.slug)
    .slice(0, 5);
  const tags = getAllTags(true).slice(0, 14);

  return (
    <div className="space-y-8 lg:sticky lg:top-20">
      {/* サイドバー最上部の広告枠 */}
      {showAds && <AdSlot position="sidebar" />}

      {/* 追従する収益導線 */}
      {current && <CtaBanner genreSlug={current.genreSlug} slot="secondary" />}

      {affiliates.length > 0 && <AffiliateBox links={affiliates} compact />}

      {latest.length > 0 && (
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="t-card">最新記事</h2>
          <ul className="mt-4 space-y-4">
            {latest.map((article) => (
              <li key={article.slug}>
                <Link href={`/articles/${article.slug}`} className="group block">
                  <span className="text-meta text-muted">
                    {formatDate(article.publishedAt)}
                  </span>
                  <span className="mt-1 block text-note font-medium leading-snug text-ink-soft group-hover:text-accent">
                    {displayHeadline(article.title)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      {tags.length > 0 && (
        <section className="rounded-2xl border border-line bg-surface p-5">
          <h2 className="t-card">トピック</h2>
          <div className="mt-4 flex flex-wrap gap-2">
            {tags.map((tag) => (
              <Link
                key={tag.slug}
                href={`/tags/${encodeURIComponent(tag.slug)}`}
                className="rounded-md bg-surface-2 px-2.5 py-1 text-meta text-muted hover:bg-accent-soft hover:text-accent"
              >
                #{tag.label}
              </Link>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
