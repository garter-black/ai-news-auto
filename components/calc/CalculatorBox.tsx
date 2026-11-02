import Link from "next/link";
import { CALCULATORS, type ArticleCalculator } from "@/lib/calculators";
import { getFx, getPlanProducts } from "@/lib/plan-prices";
import dynamic from "next/dynamic";

// 計算機は記事ごとに1種類だけ。使う種類の JS だけを読むよう、種類ごとに分けて読み込む（サーバーでの描画はそのまま）
const UsdJpyCalc = dynamic(() => import("@/components/calc/UsdJpyCalc"));
const PlanCostCalc = dynamic(() => import("@/components/calc/PlanCostCalc"));
const DefenseTaxCalc = dynamic(() => import("@/components/calc/DefenseTaxCalc"));

/** 計算機ごとの注意書き（数字の前提。必ず表示する） */
export const CALC_NOTES: Record<ArticleCalculator["kind"], string> = {
  "usd-jpy":
    "目安です。実際の請求額は、カード会社が決める為替（請求の確定日のレート）、海外事務手数料、サービス側の税の扱いで変わります。消費税は10%がかかる場合の計算です。",
  "ai-plan-cost":
    "目安です。料金は公式の料金ページを毎日確かめた値ですが、プランや価格は予告なく変わることがあります。申し込む前に公式ページで最新の金額を確かめてください。",
  "defense-tax":
    "国の税（防衛特別所得税・復興特別所得税）だけの計算で、住民税は入っていません。財務省「令和8年度税制改正の大綱」の税率で、1円未満は切り捨てています。実際の申告では所得税と合わせて100円未満を切り捨てるため、少しずれることがあります。個別の税額は税務署・税理士にご確認ください。",
};

/** 記事の中に置く計算機（本文の章の下、または本文の終わり） */
export default function CalculatorBox({ calc, className = "" }: { calc: ArticleCalculator; className?: string }) {
  const meta = CALCULATORS[calc.kind];
  const headingId = `calc-${calc.kind}-heading`;
  return (
    <section aria-labelledby={headingId} data-calc={calc.kind} className={`max-w-[42.5rem] rounded-2xl border border-line-strong bg-paper p-5 sm:p-6 ${className}`}>
      <p className="text-meta font-bold text-accent">計算してみる</p>
      <h2 id={headingId} className="t-h3 mt-0.5">
        {meta.title}
      </h2>
      <div className="mt-4">
        <Calculator calc={calc} />
      </div>
      <p className="mt-4 text-note leading-[1.7] text-ink-soft">{CALC_NOTES[calc.kind]}</p>
      <p className="mt-2 text-note">
        <Link href={meta.href} className="font-bold text-accent underline decoration-line-strong underline-offset-4 hover:decoration-accent">
          {meta.title}のページで開く →
        </Link>
      </p>
    </section>
  );
}

export function Calculator({ calc }: { calc: Pick<ArticleCalculator, "kind" | "product" | "plan" | "usd"> }) {
  if (calc.kind === "usd-jpy") return <UsdJpyCalc fx={getFx()} defaultUsd={calc.usd ?? 20} />;
  if (calc.kind === "ai-plan-cost") {
    const products = getPlanProducts();
    // 記事の中では、その記事の製品だけ（選ぶ手間を減らす）。単独ページは全製品
    const list = calc.product ? products.filter((p) => p.product === calc.product) : products;
    return <PlanCostCalc products={list.length ? list : products} fx={getFx()} defaultProduct={calc.product} defaultPlan={calc.plan} />;
  }
  return <DefenseTaxCalc />;
}
