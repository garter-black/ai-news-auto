import type { ReactNode } from "react";

/** 計算機の共通部品（サーバー・クライアントどちらからも使える見た目だけの部品） */

export const INPUT_CLASS =
  "mt-1.5 w-full rounded-lg border border-line-strong bg-paper px-3 py-2.5 text-desc text-ink tabular-nums focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export function Field({ id, label, help, children }: { id: string; label: string; help?: ReactNode; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="text-note font-bold text-ink">
        {label}
      </label>
      {children}
      {help ? <p className="mt-1.5 text-note leading-[1.7] text-ink-soft">{help}</p> : null}
    </div>
  );
}

/** 結果の1行（左に名前、右に大きな数字） */
export function ResultRow({ label, value, strong = false, sub }: { label: string; value: string; strong?: boolean; sub?: string }) {
  return (
    <div className={`flex items-baseline justify-between gap-4 py-2.5 ${strong ? "" : "border-b border-line"}`}>
      <dt className={`shrink-0 text-note ${strong ? "font-bold text-ink" : "text-ink-soft"}`}>{label}</dt>
      <dd className="text-right">
        <span className={`font-bold tabular-nums text-ink ${strong ? "text-[1.375rem] leading-tight" : "text-desc"}`}>{value}</span>
        {sub ? <span className="mt-0.5 block text-meta text-ink-soft">{sub}</span> : null}
      </dd>
    </div>
  );
}

/** 2択・3択の切り替え（ラジオボタン） */
export function Segmented<T extends string>({
  name,
  value,
  options,
  onChange,
  label,
}: {
  name: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="text-note font-bold text-ink">{label}</legend>
      <div className="mt-1.5 flex flex-wrap gap-2">
        {options.map((option) => (
          <label
            key={option.value}
            className={`cursor-pointer rounded-lg border px-3.5 py-2 text-note font-bold transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent ${
              value === option.value ? "border-accent bg-accent-soft text-ink" : "border-line-strong bg-paper text-ink-soft hover:border-ink"
            }`}
          >
            <input type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} className="sr-only" />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** 入力の文字列 → 数字（全角・カンマも受け付ける。読めなければ null） */
export function toNumber(text: string): number | null {
  const normalized = text.normalize("NFKC").replace(/[,，\s円$ドル]/g, "");
  if (normalized === "") return null;
  const value = Number(normalized);
  return Number.isFinite(value) && value >= 0 ? value : null;
}
