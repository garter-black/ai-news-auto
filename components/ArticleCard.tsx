import Thumb from "@/components/Thumb";
import Link from "next/link";
import Morph from "@/components/Morph";
import Image from "next/image";
import { coverPath, formatDate, isRecentlyUpdated, sourceLabel, speedLabel, type ArticleSummary } from "@/lib/articles";
import { thumbSource } from "@/lib/companies";
import { Highlight } from "@/lib/highlight";
import { displayHeadline } from "@/lib/headline";

type Props = {
  article: ArticleSummary;
  priority?: boolean;
  /** 記事ページへの画面遷移で、写真と見出しをつなぐ。ページ内で同じ記事に1回だけ使う */
  morph?: boolean;
  /** 記事末尾では生成画像より元画像を優先し、読み込み待ちを減らす */
  quickImage?: boolean;
};

/**
 * 記事カード。箱で囲まず、写真と見出しだけで構成する。
 * 全てを箱に入れると強弱が消えて、どこを見るべきか分からなくなるため。
 */
export default function ArticleCard({ article, priority, morph = false, quickImage = false }: Props) {
  const isRoundup = article.type === "roundup";
  const sources = article.sourceCount;
  const cover = quickImage && article.thumbnail ? null : coverPath(article);
  const speed = speedLabel(article);

  return (
    <article className="group">
      <Link href={`/articles/${article.slug}`} className="flex flex-col gap-3.5">
        <Morph name={`cover-${article.slug}`} enabled={morph}>
          <div className="relative aspect-[40/21] overflow-hidden rounded-xl bg-surface-2 shadow-card transition-[transform,box-shadow] duration-300 ease-out group-hover:-translate-y-[3px] group-hover:shadow-lift motion-reduce:transition-none motion-reduce:group-hover:translate-y-0">
            {cover ? (
              // 日本語見出し・企業・サイト名入りの独自カバー（統合本数もカバー内に入っている）
              <Image
                src={cover}
                alt=""
                fill
                priority={priority}
                sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
                className="object-cover transition-transform duration-500 ease-out group-hover:scale-[1.03]"
              />
            ) : (
              <Thumb
                src={article.thumbnail}
                source={thumbSource(article)}
                cover={coverPath(article)}
                aiGenerated={article.imageCredit?.kind === "ai"}
                alt=""
                fill
                priority={priority}
                sizes="(max-width: 640px) 100vw, (max-width: 1024px) 50vw, 33vw"
                className="object-cover transition-transform duration-500 ease-out group-hover:scale-[1.04]"
              />
            )}
            {!cover && (isRoundup || article.genreSlug !== "ai") && (
              <span className="absolute left-3 top-3 flex gap-1.5">
                {isRoundup && (
                  <span className="rounded-sm bg-ink/90 px-2.5 py-1 text-badge font-bold text-paper">
                    {sources > 0 ? `${sources}本を統合` : "まとめ"}
                  </span>
                )}
                {article.genreSlug !== "ai" && (
                  <span className="rounded-sm bg-paper/90 px-2.5 py-1 text-badge font-bold text-ink">
                    {article.genre}
                  </span>
                )}
              </span>
            )}
          </div>
        </Morph>

        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-meta text-muted">
            <i className={`dot ${isRoundup ? "dot-roundup" : "dot-official"}`} aria-hidden />
            <span className="font-bold">{sourceLabel(article)}</span>
            <time dateTime={article.publishedAt} className="index-num">
              {formatDate(article.publishedAt)}
            </time>
            {isRecentlyUpdated(article) && (
              <span className="rounded-sm bg-go-soft px-2 py-[1px] text-badge font-bold text-go">更新</span>
            )}
            {speed && (
              <span className="rounded-sm border border-live/40 px-2 py-[1px] text-badge font-bold text-live">{speed}</span>
            )}
          </div>

          <Morph name={`title-${article.slug}`} enabled={morph}>
            <h3 className="t-card decoration-[1.5px] underline-offset-4 group-hover:underline">
              <Highlight text={displayHeadline(article.title)} />
            </h3>
          </Morph>

          <p className="line-clamp-2 text-desc leading-[1.75] text-ink-soft">
            {article.description}
          </p>
        </div>
      </Link>
    </article>
  );
}
