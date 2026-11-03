import Link from "next/link";
import { ArticleMark } from "@/components/CompanyMark";
import DayLabel from "@/components/DayLabel";
import Thumb from "@/components/Thumb";
import { smallThumb } from "@/lib/thumb-url";
import UsableFilter from "@/components/UsableFilter";
import VerdictBadge from "@/components/VerdictBadge";
import type { ArticleSummary } from "@/lib/articles";
import { companyOf, thumbSource } from "@/lib/companies";
import { Highlight } from "@/lib/highlight";
import { isUnknownValue, listUsability, verdictKind, verdictOf } from "@/lib/verdict";
import { displayHeadline } from "@/lib/headline";
import { coverPath } from "@/lib/cover-path";

/** 48時間以内に続報を足した記事（並び順は作成日時のまま、印だけ付ける） */
const updatedRecently = (article: ArticleSummary) =>
  Boolean(article.updatedAt) && Date.now() - Date.parse(article.updatedAt!) < 48 * 3_600_000;

const clock = new Intl.DateTimeFormat("ja-JP", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Tokyo",
});
const jstDay = (iso: string) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tokyo" }).format(Date.parse(iso));

/**
 * 新着を時系列で並べる「速報の流れ」。
 * 1行に「いつ・どこが・何を・要点」を収め、記事を開かなくても流し読みで状況がつかめるようにする。
 * 要点は記事の「3秒でわかる要点」の1つ目。
 */
export default function Timeline({
  articles,
  filterable = false,
}: {
  articles: ArticleSummary[];
  /** 「今すぐ使えるものだけ」スイッチを出す */
  filterable?: boolean;
}) {
  const days: { day: string; items: ArticleSummary[] }[] = [];
  for (const article of articles) {
    const day = jstDay(article.generatedAt || article.publishedAt);
    const last = days.at(-1);
    if (last?.day === day) last.items.push(article);
    else days.push({ day, items: [article] });
  }

  const list = (
    <div className="grid gap-9">
      {days.map(({ day, items }) => (
        <section key={day} aria-label={day}>
          <h3 className="sticky top-14 z-10 -mx-1 flex items-baseline justify-between bg-paper/95 px-1 py-2 text-[1.0625rem] font-bold backdrop-blur">
            <DayLabel day={day} />
            <span className="index-num text-meta font-normal text-muted">{items.length}本</span>
          </h3>

          <ol className="border-t border-ink">
            {items.map((article) => (
              <TimelineRow key={article.slug} article={article} />
            ))}
          </ol>
        </section>
      ))}
    </div>
  );

  // 判定のある記事が一覧に無ければ、スイッチを出しても意味がない
  const hasVerdict = articles.some((article) => listUsability(article));
  return filterable && hasVerdict ? <UsableFilter>{list}</UsableFilter> : list;
}

function TimelineRow({ article }: { article: ArticleSummary }) {
  const company = companyOf(article);
  const point = article.highlights[0];
  const isRoundup = article.type === "roundup";
  // 一覧の時刻は「サイソクに載った時刻」。元動画の公開日は記事ページに出す
  const postedAt = article.generatedAt || article.publishedAt;
  const time = clock.format(Date.parse(postedAt));
  const verdict = verdictOf(article);
  // 一覧の札（使える／一部／まだ）は製品・サービスの記事だけ。スポーツ・制度・製品でないニュースには付けない
  const kind = listUsability(article);
  const verdictKindOf = verdict ? verdictKind(article) : undefined;

  return (
    <li className="tl-row border-b border-line" data-usable={kind}>
      <Link
        href={`/articles/${article.slug}`}
        className="group grid grid-cols-1 gap-x-5 py-4 sm:grid-cols-[4rem_minmax(0,1fr)_11.5rem]"
      >
        {/* 幅のある画面では時刻と判定を左の列に、スマホでは企業名の横に置いて見出しの幅を確保する */}
        <div className="hidden content-start gap-2.5 sm:grid">
          <time
            dateTime={postedAt}
            className="index-num pt-[3px] text-meta font-semibold text-muted"
          >
            {time}
          </time>
          {kind && <VerdictBadge kind={kind} block />}
        </div>

        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta">
            {kind && (
              <span className="sm:hidden">
                <VerdictBadge kind={kind} />
              </span>
            )}
            <span className="index-num font-semibold text-muted sm:hidden">{time}</span>
            <ArticleMark company={company} genre={article.genre} size={16} />
            {isRoundup && (
              <span className="rounded-sm bg-ink px-2 py-[1px] text-badge font-bold text-paper">
                まとめ {article.sourceCount}本
              </span>
            )}
            {updatedRecently(article) && (
              <span className="rounded-sm bg-go-soft px-2 py-[1px] text-badge font-bold text-go">更新</span>
            )}
          </p>

          <h4 className="t-card mt-1.5 decoration-1 underline-offset-4 group-hover:underline">
            <Highlight text={displayHeadline(article.title)} />
          </h4>

          {verdict && verdictKindOf === "product" && (
            <p className="mt-1 text-note text-muted">
              {[!isUnknownValue(verdict.price) ? verdict.price : "", verdict.japan === "ok" ? "日本から利用可" : verdict.japan === "no" && kind !== "wait" ? "日本では未提供" : "", !isUnknownValue(verdict.audience) ? `${verdict.audience}向け` : ""]
                .filter(Boolean)
                .join(" ・ ")}
            </p>
          )}
          {/* スポーツ・制度は「日本から利用可」のような製品の言い方をせず、日時と金額だけ */}
          {verdict && verdictKindOf !== "product" && [verdict.when, verdict.price].some((v) => !isUnknownValue(v)) && (
            <p className="mt-1 text-note text-muted">
              {[verdict.when, verdict.price].filter((v) => !isUnknownValue(v)).join(" ・ ")}
            </p>
          )}

          {point && (
            <p className="mt-1.5 line-clamp-2 text-desc leading-[1.75] text-ink-soft">
              <span aria-hidden className="mr-1.5 font-bold text-accent">
                要点
              </span>
              {point}
            </p>
          )}
        </div>

        {/* スマホでは画像を省き、文字だけで速く流し読みできるようにする */}
        <div className="relative hidden aspect-video overflow-hidden rounded-lg bg-surface-2 sm:block">
          <Thumb
            // 一覧の小さな枠には軽い画像（480px）を使う。最高画質（1280px）は記事ページだけ
            src={smallThumb(article.thumbnail)}
            source={thumbSource(article)}
            cover={coverPath(article)}
            alt=""
            fill
            sizes="184px"
            className="object-cover transition-transform duration-500 group-hover:scale-[1.04]"
          />
        </div>
      </Link>
    </li>
  );
}
