/**
 * 「今日やること」（2〜3個の番号つきの行動）。本文を読み終えたところに置く。
 * 中身は scripts/web_articles.py の today_actions（元記事で確かめられる行動だけ。画面の場所は作らない）。
 */
export default function TodayActions({ actions }: { actions: string[] }) {
  return (
    <section aria-labelledby="today-actions-heading" className="mt-12 max-w-[40em] rounded-2xl border border-line-strong p-5 sm:p-6">
      <h2 id="today-actions-heading" className="t-h3">
        今日やること
      </h2>
      <ol className="mt-3.5 grid gap-2.5">
        {actions.map((action, index) => (
          <li key={index} className="grid grid-cols-[24px_1fr] gap-3 text-desc font-medium leading-[1.75] text-ink">
            <span className="index-num mt-0.5 grid size-6 place-items-center rounded-full bg-ink text-meta font-semibold text-paper">
              {index + 1}
            </span>
            {action}
          </li>
        ))}
      </ol>
    </section>
  );
}
