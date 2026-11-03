import VerdictIcon from "@/components/VerdictIcon";
import { cleanTake } from "@/lib/article-sections";
import {
  STATUS_LABEL_BY_KIND,
  VERDICT_HEADING,
  usability,
  isUnknownValue,
  verdictFacts,
  type Verdict,
  type VerdictKind,
} from "@/lib/verdict";

const dateFormat = new Intl.DateTimeFormat("ja-JP", {
  month: "numeric",
  day: "numeric",
  timeZone: "Asia/Tokyo",
});

const TONE = {
  go: "bg-go-soft text-go",
  partial: "bg-surface-2 text-ink",
  wait: "bg-surface-2 text-muted",
} as const;

/**
 * 記事冒頭の「使える？」判定カード。
 * 発表を読んだ人が最初に知りたい「自分は今これを使えるのか」に一目で答える。
 */
export default function VerdictCard({
  verdict,
  judgedAt,
  cardKind = "product",
}: {
  verdict: Verdict;
  judgedAt: string;
  /** スポーツ・制度は問いを変える（lib/verdict.ts の verdictKind） */
  cardKind?: VerdictKind;
}) {
  const kind = usability(verdict);
  // 「記載なし」「不明」の欄は出さない（並ぶと壊れて見える）。残った欄の数で並びを決める
  // スポーツ・制度の「観戦・参加できる？」は上の札と同じ言葉なので重ねない
  const facts = verdictFacts(verdict, cardKind).filter(
    (fact) => !isUnknownValue(fact.value) && fact.value !== STATUS_LABEL_BY_KIND[cardKind][verdict.status],
  );
  const cols = facts.length >= 4 ? "sm:grid-cols-4" : facts.length === 3 ? "sm:grid-cols-3" : "";
  const japanSource = cardKind === "product" ? verdict.japanSource : undefined;
  const take = cleanTake(verdict.take ?? "");

  return (
    <section aria-labelledby="verdict-heading" className="mt-6 overflow-hidden rounded-2xl border border-line-strong">
      <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-start sm:gap-5 sm:p-6">
        <div className={`flex shrink-0 items-center gap-2 self-start rounded-xl px-3.5 py-2.5 ${TONE[kind]}`}>
          <VerdictIcon kind={kind} size={22} />
          <span className="text-[15px] font-bold leading-none">{STATUS_LABEL_BY_KIND[cardKind][verdict.status]}</span>
        </div>
        <div className="min-w-0">
          <h2 id="verdict-heading" className="t-h3">
            {VERDICT_HEADING[cardKind]}
          </h2>
          {/* 答えになっていない文を除き、「〜しましょう」の呼びかけは落ち着いた言い方にして出す（lib/article-sections.ts の cleanTake。2026-11-03） */}
          {take && <p className="mt-1 text-[16px] font-bold leading-[1.65] text-ink text-balance">{take}</p>}
        </div>
      </div>

      {facts.length > 0 && (
        <dl className={`grid grid-cols-2 border-t border-line ${cols}`}>
          {facts.map((fact, index) => {
            // スマホは2列。奇数個の最後の欄は横いっぱいにして、空いた枠を作らない
            const last = index === facts.length - 1 && facts.length % 2 === 1;
            return (
              <div
                key={fact.label}
                className={`px-5 py-3.5 sm:px-6 ${last ? "col-span-2 sm:col-span-1" : ""} ${index % 2 === 1 ? "border-l border-line" : ""} ${
                  index >= 2 ? "border-t border-line sm:border-t-0" : ""
                } ${index >= 2 && index % 2 === 0 ? "sm:border-l" : ""}`}
              >
                <dt className="text-meta text-muted">{fact.label}</dt>
                <dd className={`mt-0.5 text-note font-bold leading-snug ${fact.muted ? "text-muted" : "text-ink"}`}>
                  {fact.value}
                </dd>
              </div>
            );
          })}
        </dl>
      )}

      <p className="border-t border-line bg-surface px-5 py-2 text-note text-muted sm:px-6">
        {dateFormat.format(Date.parse(judgedAt))}時点の判定。発表内容をもとにAIで判定しています。最新の提供状況は公式情報をご確認ください。
        {japanSource?.url && (
          <>
            {" "}日本での提供は当サイトが
            <a href={japanSource.url} target="_blank" rel="noopener noreferrer" className="mx-0.5 underline underline-offset-2">
              公式ページ
            </a>
            で確認（{japanSource.checkedAt.slice(5).replace("-", "/").replace(/^0/, "").replace("/0", "/")}）。
          </>
        )}
      </p>
    </section>
  );
}
