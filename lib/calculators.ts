import type { ArticleChart } from "@/lib/charts";

/**
 * 記事に合う計算機（1記事に1つだけ）。本文の合う章の下に置き、無ければ本文の終わりに置く。
 * 計算機そのものは /tools/* の単独ページと同じ部品（components/calc）。
 */
/**
 * 2026-11-03 オーナー「このページもいらない」で /tools/usd-jpy（ドル建てサブスクの円換算）を終了した（台帳 W12。URL は cloudflare/worker.ts で 410）。
 * ドルの月額に為替と消費税を掛けるだけで、料金の段の表（円の目安つき）と重なり、記事への埋め込みも料金の話に合っていなかった。
 * 計算ツールは「公式の数字から読者自身の金額を出す」ものだけ置く（いまは防衛特別所得税だけ）。
 */
export type CalculatorKind = "defense-tax";

export type ArticleCalculator = {
  kind: CalculatorKind;
  /** この見出しの章の下に置く（無ければ本文の終わり） */
  heading: RegExp | null;
};

export const CALCULATORS: Record<CalculatorKind, { href: string; title: string; description: string }> = {
  "defense-tax": {
    href: "/tools/defense-tax",
    title: "防衛特別所得税の計算",
    description: "所得税額（基準所得税額）から、2027年からの防衛特別所得税と復興特別所得税を年ごとに計算します。",
  },
};

type ArticleLike = {
  title: string;
  seoTitle?: string;
  tags: string[];
  genreSlug?: string;
  body?: string;
  verdict?: { price?: string } | null;
  charts?: ArticleChart[];
};

const TAX_WORDS = /防衛特別所得税|防衛増税|防衛税|復興特別所得税/;

/**
 * AIの有料プランの費用計算（/tools/ai-plan-cost）は 2026-11-03 に終了した（「12か月で16,800円」「Plusの0.5個分」のような
 * 足し算・割り算だけで、読者に新しい情報が無かった。AdSense の「有用性の低いコンテンツ」対策。URL は cloudflare/worker.ts で 410）。
 * 料金の段の図がある記事は、図に円の目安が出ているので計算機を置かない。
 * 2つ目の引数は使っていない（呼び出し側の互換のため残している。呼び出し側から外したら消してよい）
 */
export function calculatorForArticle(article: ArticleLike): ArticleCalculator | null {
  const title = `${article.title} ${article.seoTitle ?? ""}`;
  // 税の記事（防衛特別所得税・復興特別所得税）
  if (TAX_WORDS.test(title) || article.tags.some((t) => TAX_WORDS.test(t))) {
    return { kind: "defense-tax", heading: /仕組み|いくら|負担|税率|数字|数値|変わる/ };
  }
  return null;
}
