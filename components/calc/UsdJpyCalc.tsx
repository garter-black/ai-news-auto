"use client";

import { useId, useState } from "react";
import { usdToYen, yen } from "@/lib/calc-math";
import type { Fx } from "@/lib/plan-prices";
import { Field, INPUT_CLASS, ResultRow, Segmented, toNumber } from "@/components/calc/parts";

/**
 * ドル建てのサブスク → 円（消費税10%・年額・カードの海外手数料）。
 * 入力は画面の中の計算だけに使い、送信・保存はしない。
 */
export default function UsdJpyCalc({ fx, defaultUsd = 20 }: { fx: Fx; defaultUsd?: number }) {
  const id = useId();
  const [usd, setUsd] = useState(String(defaultUsd));
  const [rate, setRate] = useState(String(fx.rate));
  const [tax, setTax] = useState<"yes" | "no">("yes");
  const [fee, setFee] = useState("0");

  const usdValue = toNumber(usd) ?? 0;
  const rateValue = toNumber(rate) ?? fx.rate;
  const feeValue = Math.min(10, toNumber(fee) ?? 0);
  const monthly = usdToYen(usdValue, rateValue, tax === "yes", feeValue);
  const yearly = usdToYen(usdValue * 12, rateValue, tax === "yes", feeValue);

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id={`${id}-usd`} label="月額（ドル）">
          <input id={`${id}-usd`} type="text" inputMode="decimal" autoComplete="off" value={usd} onChange={(e) => setUsd(e.target.value)} className={INPUT_CLASS} />
        </Field>
        <Field
          id={`${id}-rate`}
          label="為替（1ドル＝円）"
          help={`${fx.fallback ? "控えの値" : "毎日更新"}：${fx.rate}円（${fx.date}・${fx.source}）。変えられます。`}
        >
          <input id={`${id}-rate`} type="text" inputMode="decimal" autoComplete="off" value={rate} onChange={(e) => setRate(e.target.value)} className={INPUT_CLASS} />
        </Field>
        <Segmented
          name={`${id}-tax`}
          label="消費税10%"
          value={tax}
          onChange={setTax}
          options={[
            { value: "yes", label: "足す" },
            { value: "no", label: "足さない" },
          ]}
        />
        <Field id={`${id}-fee`} label="カードの海外手数料（%・任意）" help="カード会社ごとに違います。分からなければ0のままで。">
          <input id={`${id}-fee`} type="text" inputMode="decimal" autoComplete="off" value={fee} onChange={(e) => setFee(e.target.value)} className={INPUT_CLASS} />
        </Field>
      </div>

      <dl className="mt-5 rounded-xl bg-surface px-4 py-1.5" aria-live="polite">
        <ResultRow label="税抜き" value={yen(monthly.base)} />
        {tax === "yes" && <ResultRow label="消費税（10%）" value={yen(monthly.tax)} />}
        {feeValue > 0 && <ResultRow label={`海外手数料（${feeValue}%）`} value={yen(monthly.fee)} />}
        <ResultRow label="1か月あたり" value={`約${yen(monthly.total)}`} strong sub={`1年（12か月）で 約${yen(yearly.total)}`} />
      </dl>
    </div>
  );
}
