import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import ToolPage, { ToolSection } from "@/components/calc/ToolPage";
import { CALCULATORS } from "@/lib/calculators";
import { getFx } from "@/lib/plan-prices";

const meta = CALCULATORS["usd-jpy"];

export const metadata: Metadata = withPageShare("usd-jpy", meta.href, {
  title: "ドル建てサブスクの円換算｜消費税10%・年額つき",
  description: meta.description,
  alternates: { canonical: meta.href },
});

export default function UsdJpyPage() {
  const fx = getFx();
  return (
    <ToolPage kind="usd-jpy" lead="ChatGPT・Claude などドルで請求されるサブスクが、円でいくらになるかの目安。為替は毎日の参照レートを初期値にしています。">
      <ToolSection title="計算のしかた">
        <p>円（税抜き）＝ドルの金額 × 為替。消費税＝そこに10%。カードの海外手数料は、税込みの金額にかかるものとして足します。1円未満は四捨五入です。</p>
        <p>
          初期値の為替は1ドル＝{fx.rate}円（{fx.date}、{fx.source}）。{fx.fallback ? "毎日の更新データが無いときの控えの値です。" : "毎日更新しています。"}カード会社が実際に使うレートとは少し違います。
        </p>
      </ToolSection>
      <ToolSection title="消費税はかかる？">
        <p>海外の会社のネットのサービスでも、日本の利用者には消費税10%がかかるのが原則です。請求の画面に税が別に出るか、税込みの価格かはサービスごとに違うので、最後は申し込み画面の金額で確かめてください。</p>
      </ToolSection>
    </ToolPage>
  );
}
