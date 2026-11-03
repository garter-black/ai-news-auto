import Link from "next/link";
import { formatDateTime, reviewedDateLabel, type FullArticle } from "@/lib/articles";
import { isNewsOutletChannel, TRENDS } from "@/lib/news-outlets";

/**
 * 記事末の「出典と更新履歴」。どの公式発表をもとに、いつ載せ、いつ何を足したかを1か所で見せる。
 * 発表から何分かは推定を含むため出さず、時刻だけを並べる（docs/latency-evidence-20261031.md）。
 */
/** 2026-11-03 → 11/3 */
function shortDate(value: string): string {
  const m = /^\d{4}-(\d{2})-(\d{2})/.exec(value);
  return m ? `${Number(m[1])}/${Number(m[2])}` : value;
}

export default function SourceRecord({
  article,
  sourceUrl,
  sourceName,
  guide,
}: {
  article: FullArticle;
  sourceUrl?: string;
  sourceName: string;
  guide?: { href: string; label: string };
}) {
  const official = article.sourcePublishedAt && /T\d/.test(article.sourcePublishedAt) ? article.sourcePublishedAt : "";
  const updates = article.updates ?? [];
  // 話題の見張り（radar-）から来た記事は報道が元のことがある。公式・官公庁の名前でなければ「報道」と表示する
  // 報道機関の発信元（lib/news-outlets.ts）は必ず「報道」。話題のまとめ（trends）は報道と公式が混ざるので「出典」だけ（台帳 W12）
  const mixed = article.channelSlug === TRENDS;
  const reported =
    !mixed &&
    (isNewsOutletChannel(article.channelSlug) ||
      ((article.channelSlug ?? "").startsWith("radar-") && !/公式|省|庁|委員会|政府|都庁|県|市役所/.test(sourceName)));
  const row = "grid gap-1 py-3 sm:grid-cols-[9rem_1fr] sm:gap-4";
  const key = "text-meta font-bold text-muted";
  const link = "font-semibold text-ink underline decoration-line-strong underline-offset-4 hover:text-accent";

  return (
    <section aria-labelledby="record-heading" className="mt-12 max-w-[46em] rounded-2xl border border-line px-5 py-4 sm:px-6">
      <h2 id="record-heading" className="t-h3">
        出典と更新履歴
      </h2>
      <dl className="mt-2 divide-y divide-line text-note leading-relaxed text-ink-soft">
        {sourceUrl && (
          <div className={row}>
            <dt className={key}>{article.sourceType === "web" ? (mixed ? "出典" : reported ? "出典（報道）" : "出典（公式）") : "元の動画"}</dt>
            <dd>
              <a href={sourceUrl} target="_blank" rel="noopener noreferrer" className={link}>
                {sourceName}
              </a>
              {article.videoTitle ? <span className="ml-1 text-muted">「{article.videoTitle}」</span> : null}
            </dd>
          </div>
        )}
        {(article.factSources ?? []).length > 0 && (
          <div className={row}>
            <dt className={key}>確かめた資料</dt>
            <dd>
              <ul className="grid gap-1.5">
                {(article.factSources ?? []).map((s) => (
                  <li key={`${s.label}-${s.name}`}>
                    {s.label}：
                    {s.url ? (
                      s.url.startsWith("/") ? (
                        <Link href={s.url} className={link}>
                          {s.name}
                        </Link>
                      ) : (
                        <a href={s.url} target="_blank" rel="noopener noreferrer" className={link}>
                          {s.name}
                        </a>
                      )
                    ) : (
                      s.name
                    )}
                    {s.checkedAt && <span className="index-num text-muted">（{shortDate(s.checkedAt)}確認）</span>}
                  </li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        <div className={row}>
          <dt className={key}>日時</dt>
          <dd className="index-num">
            {official && formatDateTime(official) && (
              <>
                {mixed ? "発表" : reported ? "報道" : "公式発表"} <time dateTime={official}>{formatDateTime(official)}</time>
                <span aria-hidden> → </span>
              </>
            )}
            サイソク掲載 <time dateTime={article.generatedAt}>{formatDateTime(article.generatedAt)}</time>
            {article.updatedAt && formatDateTime(article.updatedAt) && (
              <>
                <span aria-hidden>／</span>最終更新 <time dateTime={article.updatedAt}>{formatDateTime(article.updatedAt)}</time>
              </>
            )}
          </dd>
        </div>
        <div className={row}>
          <dt className={key}>確認方法</dt>
          <dd>
            公開前に、本文を出典と自動で照合しています
            {typeof article.qualityScore === "number" ? `（事実確認 ${article.qualityScore}点／100点、70点未満は非公開）` : ""}。
            {reviewedDateLabel(article) && `${reviewedDateLabel(article)}に、編集部が記事を読んで確認しました。`}
            <Link href="/editorial-policy" className={`${link} ml-1`}>
              制作と確認の方法
            </Link>
          </dd>
        </div>
        {updates.length > 0 && (
          <div className={row}>
            <dt className={key}>更新履歴</dt>
            <dd>
              <ol className="grid gap-1.5">
                {[...updates].reverse().map((u) => (
                  <li key={`${u.at}-${u.sourceUrl}`}>
                    <time dateTime={u.at} className="index-num text-muted">
                      {formatDateTime(u.at)}
                    </time>{" "}
                    {u.title}
                    {u.sourceUrl && (
                      <>
                        （
                        <a href={u.sourceUrl} target="_blank" rel="noopener noreferrer" className={link}>
                          {u.sourceName || "出典"}
                        </a>
                        ）
                      </>
                    )}
                  </li>
                ))}
              </ol>
            </dd>
          </div>
        )}
        {guide && (
          <div className={row}>
            <dt className={key}>関連ガイド</dt>
            <dd>
              <Link href={guide.href} className={link}>
                {guide.label}
              </Link>
            </dd>
          </div>
        )}
        <div className={row}>
          <dt className={key}>誤りの報告</dt>
          <dd>
            <Link href="/contact" className={link}>
              お問い合わせ
            </Link>
            から受け付けています。直した記事は
            <Link href="/corrections" className={`${link} mx-1`}>
              訂正履歴
            </Link>
            に残します。
          </dd>
        </div>
      </dl>
    </section>
  );
}
