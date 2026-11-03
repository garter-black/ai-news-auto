import Link from "next/link";
import Image from "next/image";
import Thumb from "@/components/Thumb";
import VerdictBadge from "@/components/VerdictBadge";
import { coverPath, type NextRead as NextReadItem } from "@/lib/articles";
import { thumbSource } from "@/lib/companies";
import { listUsability } from "@/lib/verdict";
import { displayHeadline } from "@/lib/headline";
import { Phrases } from "@/lib/highlight";

/**
 * 記事を読み終えた位置に置く「次に読むならこれ」。
 * 1本目を大きく見せて、もう1ページ読んでもらう（最初の1ページで帰る人を減らす）。
 */
export default function NextRead({ items }: { items: NextReadItem[] }) {
  if (items.length === 0) return null;
  const [first, ...rest] = items;
  return (
    <section aria-labelledby="next-read-heading" className="mt-12 max-w-[46em]">
      <h2 id="next-read-heading" className="t-h2 flex items-center gap-2">
        次に読む記事
        <span aria-hidden className="text-live">→</span>
      </h2>

      <Link
        href={`/articles/${first.article.slug}`}
        className="group mt-4 grid overflow-hidden rounded-2xl border border-ink bg-paper transition-shadow hover:shadow-[0_10px_30px_-18px_rgb(0_0_0/0.5)] sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]"
      >
        <Cover item={first} sizes="(max-width: 640px) 100vw, 420px" />
        <span className="flex flex-col gap-2 p-5">
          <Meta item={first} />
          <span className="text-[17px] font-bold leading-[1.55] text-ink text-balance group-hover:underline">
            <Phrases text={displayHeadline(first.article.title)} inline />
          </span>
          {first.article.highlights[0] && (
            <span className="line-clamp-3 text-desc leading-[1.75] text-ink-soft">{first.article.highlights[0]}</span>
          )}
          <span className="mt-auto inline-flex items-center gap-1 pt-1 text-meta font-bold text-live">
            続きを読む <span aria-hidden>→</span>
          </span>
        </span>
      </Link>

      {rest.length > 0 && (
        <ul className="mt-3 grid gap-3 sm:grid-cols-2">
          {rest.map((item) => (
            <li key={item.article.slug}>
              <Link
                href={`/articles/${item.article.slug}`}
                className="group grid h-full grid-cols-[112px_minmax(0,1fr)] gap-3 rounded-xl border border-line p-2.5 hover:border-ink"
              >
                <Cover item={item} sizes="112px" small />
                <span className="flex min-w-0 flex-col gap-1">
                  <Meta item={item} />
                  <span className="line-clamp-3 text-note font-bold leading-snug text-ink group-hover:underline">
                    <Phrases text={displayHeadline(item.article.title)} inline />
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Meta({ item }: { item: NextReadItem }) {
  const usable = listUsability(item.article);
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="rounded-sm bg-ink px-2 py-[2px] text-badge font-bold text-paper">{item.reason}</span>
      {usable && <VerdictBadge kind={usable} />}
    </span>
  );
}

function Cover({ item, sizes, small = false }: { item: NextReadItem; sizes: string; small?: boolean }) {
  const cover = item.article.thumbnail ? null : coverPath(item.article);
  return (
    <span className={`relative block overflow-hidden bg-surface-2 ${small ? "aspect-[40/21] self-start rounded-lg" : "aspect-[40/21] sm:aspect-auto sm:min-h-full"}`}>
      {cover ? (
        <Image src={cover} alt="" fill sizes={sizes} className="object-contain" />
      ) : (
        <Thumb src={item.article.thumbnail} source={thumbSource(item.article)} cover={coverPath(item.article)} alt="" fill sizes={sizes} className="object-contain" />
      )}
    </span>
  );
}
