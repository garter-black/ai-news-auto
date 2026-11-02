"use client";

import { useId, useState } from "react";
import { baseFromWithheld, surtax, yen } from "@/lib/calc-math";
import { Field, INPUT_CLASS, Segmented, toNumber } from "@/components/calc/parts";

/** 年ごとの区切り（財務省「令和8年度税制改正の大綱」） */
const PERIODS = [
  { label: "2026年分まで", year: 2026 },
  { label: "2027〜2037年分", year: 2030 },
  { label: "2038〜2047年分", year: 2040 },
  { label: "2048年分から", year: 2050 },
];

/**
 * 防衛特別所得税・復興特別所得税（国の税）の計算。基準所得税額×税率（1円未満切り捨て）。
 * 入力は画面の中の計算だけに使い、送信・保存はしない。
 */
export default function DefenseTaxCalc({ defaultBase = 150000 }: { defaultBase?: number }) {
  const id = useId();
  const [mode, setMode] = useState<"base" | "withheld">("base");
  const [amount, setAmount] = useState(String(defaultBase));
  const value = toNumber(amount) ?? 0;
  const base = mode === "base" ? Math.floor(value) : baseFromWithheld(value);

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Segmented
          name={`${id}-mode`}
          label="入れる数字"
          value={mode}
          onChange={setMode}
          options={[
            { value: "base", label: "所得税額" },
            { value: "withheld", label: "源泉徴収票の税額" },
          ]}
        />
        <Field
          id={`${id}-amount`}
          label={mode === "base" ? "1年の所得税額（基準所得税額・円）" : "源泉徴収票の「源泉徴収税額」（円）"}
          help={
            mode === "base"
              ? "確定申告書の「基準所得税額」の欄の金額です。"
              : `復興特別所得税（2.1%）を含んだ金額なので、1.021で割って所得税額の目安（${yen(base)}）にします。`
          }
        >
          <input id={`${id}-amount`} type="text" inputMode="numeric" autoComplete="off" value={amount} onChange={(e) => setAmount(e.target.value)} className={INPUT_CLASS} />
        </Field>
      </div>

      <ul className="mt-5 divide-y divide-line rounded-xl bg-surface px-4" aria-live="polite" aria-label={`年ごとの税額（所得税額 ${yen(base)} のとき）`}>
        {PERIODS.map((period) => {
          const now = surtax(base, period.year);
          const before = surtax(base, period.year, true);
          const diff = now.total - before.total;
          const parts = [
            now.defense ? `防衛 ${yen(now.defense)}（1%）` : "",
            now.reconstruction ? `復興 ${yen(now.reconstruction)}（${period.year <= 2026 ? "2.1" : "1.1"}%）` : "",
          ].filter(Boolean);
          return (
            <li key={period.label} className="py-3">
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-note font-bold text-ink">{period.label}</span>
                <span className="text-[1.1875rem] font-bold tabular-nums text-ink">{yen(now.total)}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 text-note tabular-nums text-ink-soft">
                <span>{parts.length ? parts.join(" ＋ ") : "かからない"}</span>
                <span>
                  改正前のきまり {yen(before.total)}
                  {diff !== 0 && <span className="ml-1.5 font-bold text-signal">{diff > 0 ? "+" : "−"}{yen(Math.abs(diff))}</span>}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-note leading-[1.7] text-ink-soft">
        2027〜2037年分は、防衛1%と復興1.1%の合計2.1%で、今までの復興2.1%と同じです。違いが出るのは、復興特別所得税が延長される2038〜2047年分と、防衛特別所得税だけが残る2048年分からです。
      </p>
    </div>
  );
}
