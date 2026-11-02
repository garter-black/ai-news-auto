import type { Metadata } from "next";
import ToolPage, { ToolSection } from "@/components/calc/ToolPage";
import { CALCULATORS } from "@/lib/calculators";

const meta = CALCULATORS["defense-tax"];

export const metadata: Metadata = {
  title: "防衛特別所得税の計算｜2027年から・年ごとの税額",
  description: meta.description,
  alternates: { canonical: meta.href },
};

export default function DefenseTaxPage() {
  return (
    <ToolPage kind="defense-tax" lead="2027年分から始まる防衛特別所得税と、税率が変わる復興特別所得税を、所得税額から年ごとに計算します。">
      <ToolSection title="きまり（財務省の資料）">
        <p>防衛特別所得税＝その年の基準所得税額×1%。2027年（令和9年）分から当分の間です。</p>
        <p>復興特別所得税は、2027年分から税率を2.1%から1.1%に下げ、課税する期間を2037年までから2047年までに延ばします。</p>
        <p>
          出典：
          <a href="https://www.mof.go.jp/tax_policy/tax_reform/outline/fy2026/08taikou_06.htm" target="_blank" rel="noopener" className="text-accent underline underline-offset-4">
            財務省「令和8年度税制改正の大綱」
          </a>
          、
          <a href="https://www.nta.go.jp/publication/pamph/gensen/zeigakuhyo2027/data/00.pdf" target="_blank" rel="noopener" className="text-accent underline underline-offset-4">
            国税庁「令和9年分 源泉徴収税額表」
          </a>
          （2026-11-03確認）
        </p>
      </ToolSection>
      <ToolSection title="基準所得税額とは">
        <p>所得税から、住宅ローン控除などの税額控除を引いたあとの金額です。確定申告書に欄があります。会社員の源泉徴収票の「源泉徴収税額」は、これに復興特別所得税を足した金額なので、1.021で割ると目安になります。</p>
      </ToolSection>
    </ToolPage>
  );
}
