import type { Metadata } from "next";
import Link from "next/link";
import ToolPage, { ToolSection } from "@/components/calc/ToolPage";
import { CALCULATORS } from "@/lib/calculators";
import { getPlanProducts } from "@/lib/plan-prices";

const meta = CALCULATORS["ai-plan-cost"];

export const metadata: Metadata = {
  title: "AIの有料プラン費用計算｜月払いと年払い・Pro何個分",
  description: meta.description,
  alternates: { canonical: meta.href },
};

export default function AiPlanCostPage() {
  const products = getPlanProducts();
  return (
    <ToolPage kind="ai-plan-cost" lead="Claude・ChatGPT などの有料プランは、月払いと年払いのどちらが得か。上位プランは標準プランの何個分の値段か。公式の料金ページの数字で計算します。">
      <ToolSection title="使っている料金">
        <p>
          {products.map((p) => p.name).join("・")}の個人向けプランです。各社の公式の料金ページの数字で、確認日を計算機の下に出しています。チーム・法人向けのプランは1人あたりの料金なので入れていません。
        </p>
        <p>
          「何個分」は値段の比べ方です。使える量は、公式の説明に「Proの10倍の使用量」のように数字があるときだけ並べて出します。
          全体の料金は<Link href="/ai-pricing" className="text-accent underline underline-offset-4">AIの料金・日本対応 早見表</Link>でも見られます。
        </p>
      </ToolSection>
    </ToolPage>
  );
}
