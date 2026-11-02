"use client";

import { useId, useState } from "react";
import { compareBilling, CONSUMPTION_TAX, timesOf, usageMultiple, yen } from "@/lib/calc-math";
import { formatMoney } from "@/lib/money";
import type { Fx, Plan, PlanPrice, PlanProduct } from "@/lib/plan-prices";
import { Field, INPUT_CLASS, ResultRow, Segmented } from "@/components/calc/parts";

/**
 * AI の有料プランの費用：月払いと年払いの比較、標準プラン（Pro など）の何個分か。
 * 数字は content/pricing（公式の料金ページ）から。円はドルに為替を掛けた目安。入力は送信・保存しない。
 */
export default function PlanCostCalc({
  products,
  fx,
  defaultProduct,
  defaultPlan,
}: {
  products: PlanProduct[];
  fx: Fx;
  defaultProduct?: string;
  defaultPlan?: string;
}) {
  const id = useId();
  const initial = products.find((p) => p.product === defaultProduct) ?? products[0];
  const [productId, setProductId] = useState(initial?.product ?? "");
  const product = products.find((p) => p.product === productId) ?? initial;
  const paid = (product?.plans ?? []).filter((p) => p.prices.some((x) => x.amount > 0));
  const [planId, setPlanId] = useState(defaultPlan && paid.some((p) => p.id === defaultPlan) ? defaultPlan : paid[0]?.id ?? "");
  const plan = paid.find((p) => p.id === planId) ?? paid[0];
  const [months, setMonths] = useState("12");
  const [tax, setTax] = useState<"yes" | "no">("yes");

  if (!product || !plan) return <p className="text-desc text-ink-soft">料金のデータを読み込めませんでした。</p>;

  const monthlyPrice = plan.prices.find((p) => p.billing === "monthly") ?? null;
  const annualPrice = plan.prices.find((p) => p.billing === "annual" && p.currency === (monthlyPrice?.currency ?? p.currency)) ?? null;
  const currency = (monthlyPrice ?? annualPrice)!.currency;
  const annualTotal = annualPrice ? annualPrice.billedAs ?? Math.round(annualPrice.amount * 12 * 100) / 100 : null;
  const m = Math.max(1, Math.min(12, Number(months) || 12));
  const billing = monthlyPrice ? compareBilling(monthlyPrice.amount, annualTotal, m) : null;

  // 円の目安（ドルは為替と消費税、円の公式価格はそのまま）
  const toYen = (amount: number) => (currency === "JPY" ? amount : amount * fx.rate * (tax === "yes" ? 1 + CONSUMPTION_TAX : 1));
  const money = (amount: number) => (currency === "JPY" ? yen(amount) : formatMoney(amount, "USD"));
  // ドルの金額の円の目安（10円単位）。円の公式価格のときは出さない
  const yenNote = (amount: number) => (currency === "JPY" ? "" : `約${yen(Math.round(toYen(amount) / 10) * 10)}${tax === "yes" ? "（税込みの目安）" : "（税抜きの目安）"}`);

  // 同じ製品の「標準」の段（Pro・Plus・AI Pro など）と比べる
  const reference = product.plans.find((p) => p.tier === "standard" && p.id !== plan.id);
  const refPrice = reference ? monthlyOf(reference, currency, fx) : null;
  const ownPrice = monthlyOf(plan, currency, fx);
  const times = refPrice !== null && ownPrice !== null ? timesOf(ownPrice, refPrice) : null;
  const usage = reference ? usageMultiple(plan.limits, reference.name) : null;

  return (
    <div>
      <div className="grid gap-4 sm:grid-cols-2">
        {products.length > 1 && (
          <Field id={`${id}-product`} label="サービス">
            <select
              id={`${id}-product`}
              value={product.product}
              onChange={(e) => {
                const next = products.find((p) => p.product === e.target.value);
                setProductId(e.target.value);
                setPlanId(next?.plans.find((p) => p.prices.some((x) => x.amount > 0))?.id ?? "");
              }}
              className={INPUT_CLASS}
            >
              {products.map((p) => (
                <option key={p.product} value={p.product}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field id={`${id}-plan`} label="プラン">
          <select id={`${id}-plan`} value={plan.id} onChange={(e) => setPlanId(e.target.value)} className={INPUT_CLASS}>
            {paid.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}（{priceLabel(p.prices)}）
              </option>
            ))}
          </select>
        </Field>
        <Field id={`${id}-months`} label="1年のうち使う月数">
          <select id={`${id}-months`} value={String(m)} onChange={(e) => setMonths(e.target.value)} className={INPUT_CLASS}>
            {Array.from({ length: 12 }, (_, i) => i + 1).map((n) => (
              <option key={n} value={n}>
                {n}か月
              </option>
            ))}
          </select>
        </Field>
        {currency === "USD" && (
          <Segmented
            name={`${id}-tax`}
            label="円の目安に消費税10%を"
            value={tax}
            onChange={setTax}
            options={[
              { value: "yes", label: "足す" },
              { value: "no", label: "足さない" },
            ]}
          />
        )}
      </div>

      <dl className="mt-5 rounded-xl bg-surface px-4 py-1.5" aria-live="polite">
        {billing && monthlyPrice ? (
          <ResultRow
            label={`月払いで${m}か月`}
            value={money(billing.monthlyTotal)}
            strong={!annualTotal}
            sub={[yenNote(billing.monthlyTotal), `1か月 ${formatMoney(monthlyPrice.amount, currency)}`].filter(Boolean).join("・")}
          />
        ) : null}
        {annualTotal !== null ? (
          <ResultRow
            label="年払い（12か月分を一括）"
            value={money(annualTotal)}
            strong={!billing}
            sub={[yenNote(annualTotal), annualPrice ? `1か月あたり ${formatMoney(annualPrice.amount, currency)}` : ""].filter(Boolean).join("・")}
          />
        ) : null}
        {billing && billing.breakEvenMonths !== null && billing.yearlySaving !== null ? (
          <div className="py-3">
            <dt className="sr-only">どちらが安いか</dt>
            <dd className="text-desc font-bold leading-[1.7] text-ink">
              {m >= billing.breakEvenMonths
                ? `${m}か月使うなら年払いが安い（12か月使えば ${money(billing.yearlySaving)} 安い）`
                : `${m}か月だけなら月払いが安い（年払いが得になるのは${billing.breakEvenMonths}か月以上）`}
            </dd>
          </div>
        ) : (
          <div className="py-3">
            <dt className="sr-only">年払い</dt>
            <dd className="text-note leading-[1.7] text-ink-soft">このプランは公式ページに{annualTotal === null ? "年払いの価格がありません（月払いだけ）" : "月払いの価格がありません"}。</dd>
          </div>
        )}
        {reference && times !== null ? (
          <div className="border-t border-line py-3">
            <dt className="sr-only">標準プランとの比較</dt>
            <dd className="text-desc leading-[1.7] text-ink">
              値段は <strong className="font-bold">{reference.name}の{times}個分</strong>
              {usage !== null ? (
                <span className="block text-note text-ink-soft">
                  使える量は公式の説明で{reference.name}の{usage}倍{usage > times ? "。量あたりでは割安です" : usage < times ? "。量あたりでは割高です" : "。量あたりの値段は同じです"}
                </span>
              ) : (
                <span className="block text-note text-ink-soft">使える量の倍率は公式ページに数字で書かれていません。</span>
              )}
            </dd>
          </div>
        ) : null}
      </dl>
      <p className="mt-3 text-note leading-[1.7] text-ink-soft">
        出典：
        <a href={product.sourceUrl} target="_blank" rel="noopener" className="text-accent underline underline-offset-2">
          {product.sourceTitle}
        </a>
        （{product.checkedAt.slice(0, 10)}確認）。{currency === "USD" ? `円は1ドル=${fx.rate}円（${fx.date}）で換算した目安です。` : "円の価格は公式ページの表示のままです。"}
        {product.taxNote ? ` ${product.taxNote}` : ""}
      </p>
    </div>
  );
}

function priceLabel(prices: PlanPrice[]): string {
  const monthly = prices.find((p) => p.billing === "monthly") ?? prices[0];
  return `${formatMoney(monthly.amount, monthly.currency)}/月${monthly.billing === "annual" ? "・年払い時" : ""}`;
}

/** プランの1か月の値段を、指定の通貨で（無ければ為替で換算） */
function monthlyOf(plan: Plan, currency: "USD" | "JPY", fx: Fx): number | null {
  const own = plan.prices.find((p) => p.currency === currency && p.billing === "monthly") ?? plan.prices.find((p) => p.currency === currency);
  if (own) return own.amount;
  const other = plan.prices.find((p) => p.billing === "monthly") ?? plan.prices[0];
  if (!other) return null;
  return currency === "JPY" ? other.amount * fx.rate : other.amount / fx.rate;
}
