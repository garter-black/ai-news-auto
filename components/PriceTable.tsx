import { getCompany, OTHER } from "@/lib/companies";
import {
  getComparison,
  getLadder,
  type ApiTier,
  type ComparisonTable,
  type LadderTable,
  type ModelRow,
  type PlanRow,
  type PlanTier,
  type PricingProductId,
} from "@/lib/pricing-ladder";

/**
 * 料金の段の表・会社をまたいだ比較表（公式ページから毎日更新。lib/pricing-ladder.ts）。
 *   <PriceTable product="claude" />
 *   <PriceTable compare="standard" />
 *   <PriceTable compare="flagship" kind="api" />
 * スマホでは1行をカードのように縦に並べ、PC では表にする。出典・確認日・円の目安の注記を必ず付ける。
 */
type Props = { title?: string; className?: string } & (
  | { product: PricingProductId }
  | { compare: PlanTier | ApiTier; kind?: "plan" | "api" }
  | { table: LadderTable | ComparisonTable }
);

function tableOf(props: Props): LadderTable | ComparisonTable | null {
  if ("table" in props) return props.table;
  if ("product" in props) return getLadder(props.product);
  return getComparison(props.compare, props.kind ?? "plan");
}

function CompanyDot({ slug }: { slug: string }) {
  const company = getCompany(slug);
  const color = company && company.slug !== OTHER.slug ? company.color : OTHER.color;
  return <span aria-hidden className="mr-2 inline-block size-2.5 shrink-0 translate-y-[-1px] rounded-full align-middle" style={{ backgroundColor: color }} />;
}

const th = "px-4 py-3 text-left font-bold text-ink";
const cell = "sm:table-cell sm:px-4 sm:py-3.5 sm:align-top";

function Label({ children }: { children: React.ReactNode }) {
  return <span className="mb-0.5 block text-meta text-muted sm:hidden">{children}</span>;
}

function PlanRows({ rows, compare }: { rows: PlanRow[]; compare: boolean }) {
  return (
    <>
      <thead className="hidden border-b border-line bg-surface sm:table-header-group">
        <tr>
          <th scope="col" className={th}>{compare ? "サービス・プラン" : "プラン"}</th>
          <th scope="col" className={th}>料金（公式）</th>
          <th scope="col" className={th}>円の目安</th>
          <th scope="col" className={th}>主な上限・違い</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.product}-${r.id}`} className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-line px-4 py-4 last:border-0 sm:table-row sm:p-0">
            <th scope="row" className={`${cell} col-span-2 text-left font-bold text-ink`}>
              {compare && <CompanyDot slug={r.company} />}
              {compare ? r.compareName : r.name}
              {r.manual && <span className="ml-1 text-muted" title="公式ページを人が確認して記入した値">※</span>}
            </th>
            <td className={`${cell} text-ink`}>
              <Label>料金（公式）</Label>
              <ul className="grid gap-0.5">
                {(compare ? [r.mainPriceText] : r.priceTexts).map((t) => (
                  <li key={t} className="font-bold tabular-nums sm:whitespace-nowrap">{t}</li>
                ))}
              </ul>
            </td>
            <td className={`${cell} text-ink-soft`}>
              <Label>円の目安</Label>
              <span className="tabular-nums">{r.jpyText || "—"}</span>
            </td>
            <td className={`${cell} col-span-2 text-ink-soft`}>
              <Label>主な上限・違い</Label>
              {r.limits.length ? (
                <ul className="grid gap-0.5">
                  {(compare ? r.limits.slice(0, 2) : r.limits).map((t) => (
                    <li key={t}>{t}</li>
                  ))}
                </ul>
              ) : (
                "—"
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </>
  );
}

function ModelRows({ rows, compare }: { rows: ModelRow[]; compare: boolean }) {
  return (
    <>
      <thead className="hidden border-b border-line bg-surface sm:table-header-group">
        <tr>
          <th scope="col" className={th}>モデル</th>
          <th scope="col" className={th}>入力</th>
          <th scope="col" className={th}>出力</th>
          <th scope="col" className={th}>キャッシュ読み込み</th>
          <th scope="col" className={th}>円の目安（入力／出力・税別）</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.product}-${r.id}-${r.context ?? ""}`} className="grid grid-cols-2 gap-x-4 gap-y-2 border-b border-line px-4 py-4 last:border-0 sm:table-row sm:p-0">
            <th scope="row" className={`${cell} col-span-2 text-left font-bold text-ink`}>
              {compare && <CompanyDot slug={r.company} />}
              {r.name}
              {r.context && <span className="ml-1 block text-meta font-normal text-muted sm:inline">（{r.context}）</span>}
              {r.scheduledText && <span className="mt-1 block text-meta font-normal text-official">予定：{r.scheduledText}</span>}
            </th>
            {r.listed ? (
              <>
                <td className={`${cell} tabular-nums text-ink`}>
                  <Label>入力</Label>
                  <span className="font-bold">{r.inputText}</span>
                </td>
                <td className={`${cell} tabular-nums text-ink`}>
                  <Label>出力</Label>
                  <span className="font-bold">{r.outputText}</span>
                </td>
                <td className={`${cell} tabular-nums text-ink-soft`}>
                  <Label>キャッシュ読み込み</Label>
                  {r.cacheReadText}
                </td>
                <td className={`${cell} tabular-nums text-ink-soft`}>
                  <Label>円の目安（入力／出力・税別）</Label>
                  {r.jpyText || "—"}
                </td>
              </>
            ) : (
              <td colSpan={4} className={`${cell} col-span-2 text-ink-soft`}>
                料金ページに記載なし
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </>
  );
}

export default function PriceTable(props: Props) {
  const table = tableOf(props);
  if (!table || table.rows.length === 0) return null;
  const compare = !("product" in table);
  const sources = [...new Map(table.sources.map((s) => [s.url, s])).values()];
  const hasManual = table.rows.some((r) => r.kind === "plan" && r.manual);
  return (
    <figure className={`overflow-hidden rounded-2xl border border-line bg-paper ${props.className ?? ""}`}>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-4 py-3.5">
        <span className="t-card text-ink">{props.title ?? table.title}</span>
        <span className="text-meta text-muted">確認日 {table.checkedAt}</span>
      </figcaption>
      <table className="w-full text-note sm:table">
        {table.kind === "api" ? (
          <ModelRows rows={table.rows as ModelRow[]} compare={compare} />
        ) : (
          <PlanRows rows={table.rows as PlanRow[]} compare={compare} />
        )}
      </table>
      <div className="grid gap-1.5 border-t border-line bg-surface px-4 py-3.5 text-note leading-[1.8] text-ink-soft">
        <p>
          出典：
          {sources.map((s, i) => (
            <span key={s.url}>
              {i > 0 && "、"}
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
                {s.title}
              </a>
            </span>
          ))}
          （確認日：{table.checkedAt}）
        </p>
        {table.fxNote && <p>{table.fxNote}</p>}
        {"taxNote" in table && table.taxNote && <p>税：{table.taxNote}</p>}
        {hasManual && <p>※ は公式ページを機械で読めないため、人が公式ページを確認して記入した値です。</p>}
        {table.notes.map((n) => (
          <p key={n}>{n}</p>
        ))}
      </div>
    </figure>
  );
}
