import { RELEVANCE_LABEL, WORK_MONEY_HEADING, type WorkMoney } from "@/lib/work-money";

/**
 * 「仕事・お金にどう活かせる？」欄。判定カード（VerdictCard）のすぐ下に、同じ枠の形で小さく出す。
 * 読者（仕事とお金に意欲の高い働く人）が「自分はどう動けばいいか」を数秒でつかむためのもの。
 */
export default function WorkMoneyCard({ workMoney, id = "work-money-heading" }: { workMoney: WorkMoney; id?: string }) {
  return (
    <section aria-labelledby={id} className="mt-4 rounded-2xl border border-line-strong p-5 sm:p-6" data-work-money>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 id={id} className="t-h3">
          {WORK_MONEY_HEADING}
        </h2>
        <span className="rounded-full bg-surface-2 px-2.5 py-1 text-meta font-bold leading-none text-ink-soft">
          {RELEVANCE_LABEL[workMoney.relevance]}
        </span>
      </div>
      <ul className="mt-3.5 grid gap-2.5">
        {workMoney.points.map((point, index) => (
          <li key={index} className="grid grid-cols-[1rem_1fr] gap-2 text-desc font-medium leading-[1.75] text-ink">
            <span aria-hidden className="mt-[0.7em] size-1.5 rounded-full bg-ink" />
            {point}
          </li>
        ))}
      </ul>
      <p className="mt-3.5 text-note text-muted">
        発表内容をもとにした一般的な情報です。投資や契約の判断は、公式情報を確かめたうえでご自身で行ってください。
      </p>
    </section>
  );
}
