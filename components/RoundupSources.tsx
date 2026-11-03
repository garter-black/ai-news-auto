import Link from "next/link";
import { formatDate, type RoundupSource } from "@/lib/articles";

/**
 * まとめ記事が統合した元記事の一覧。
 * 読者の回遊と内部リンクの両方を担うので、記事本文の直後に置く。
 */
export default function RoundupSources({ sources }: { sources: RoundupSource[] }) {
  if (sources.length === 0) return null;

  return (
    <section className="mt-14 rounded-2xl border border-line p-6 sm:p-7">
      <p className="eyebrow">SOURCES</p>
      <h2 className="t-h2 mt-1">
        この記事が統合した {sources.length} 本の個別記事
      </h2>
      <p className="mt-2 text-desc leading-relaxed text-ink-soft">
        {sources.some((source) => source.slug)
          ? "それぞれの発表を単独で詳しく読みたい場合はこちらから。"
          : "個別の記事は、この記事に統合して取り下げました。"}
      </p>

      <ol className="mt-5 divide-y divide-line border-t border-line">
        {sources.map((source, index) => {
          const inner = (
            <>
              <span className="index-num mt-0.5 shrink-0 text-meta font-bold text-accent">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 flex-1">
                <span className={`block text-desc font-medium leading-relaxed text-ink-soft${source.slug ? " group-hover:text-accent" : ""}`}>
                  {source.title}
                </span>
                <span className="mt-1 block text-meta text-muted">
                  {source.channel}
                  {source.publishedAt && ` / ${formatDate(source.publishedAt)}`}
                </span>
              </span>
            </>
          );
          // 取り下げてこの記事に統合した元記事は slug が空（scripts/retire_articles.py）。リンクにしない
          return (
            <li key={source.slug || `source-${index}`}>
              {source.slug ? (
                <Link href={`/articles/${source.slug}`} className="group flex items-start gap-4 py-3.5">
                  {inner}
                </Link>
              ) : (
                <div className="flex items-start gap-4 py-3.5">{inner}</div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
