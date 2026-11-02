import type { ArticleChart } from "@/lib/charts";

/**
 * 記事に合う計算機（1記事に1つだけ）。本文の合う章の下に置き、無ければ本文の終わりに置く。
 * 計算機そのものは /tools/* の単独ページと同じ部品（components/calc）。
 */
export type CalculatorKind = "ai-plan-cost" | "usd-jpy" | "defense-tax";

export type ArticleCalculator = {
  kind: CalculatorKind;
  /** この見出しの章の下に置く（無ければ本文の終わり） */
  heading: RegExp | null;
  /** 料金プランの計算機の初期の製品・プラン */
  product?: string;
  plan?: string;
  /** ドル→円の初期の金額 */
  usd?: number;
};

export const CALCULATORS: Record<CalculatorKind, { href: string; title: string; description: string }> = {
  "ai-plan-cost": {
    href: "/tools/ai-plan-cost",
    title: "AIの有料プラン 費用計算",
    description: "月払いと年払いのどちらが安いか、上位プランは標準プランの何個分の値段かを、円の目安つきで計算します。",
  },
  "usd-jpy": {
    href: "/tools/usd-jpy",
    title: "ドル建てサブスクの円換算",
    description: "ドルの月額を、その日の為替と消費税10%で円にします。年額やカードの海外手数料も足せます。",
  },
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

const PRICING_SECTION = /料金|価格|値段|いくら|プラン/;
const TAX_WORDS = /防衛特別所得税|防衛増税|防衛税|復興特別所得税/;

/** 判定カードの「月200ドル」「$20/月」から金額（ドル）。幅のある書き方（〜）は使わない */
export function usdFromPrice(price: string | undefined): number | null {
  if (!price || /[〜~～]|から|以上|最大/.test(price)) return null;
  const m = price.normalize("NFKC").match(/(?:US)?\$\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*ドル/);
  if (!m) return null;
  const value = Number(m[1] ?? m[2]);
  return value > 0 && value < 100000 ? value : null;
}

export function calculatorForArticle(article: ArticleLike, knownProducts: string[]): ArticleCalculator | null {
  const title = `${article.title} ${article.seoTitle ?? ""}`;
  // 税の記事（防衛特別所得税・復興特別所得税）
  if (TAX_WORDS.test(title) || article.tags.some((t) => TAX_WORDS.test(t))) {
    return { kind: "defense-tax", heading: /仕組み|いくら|負担|税率|数字|数値|変わる/ };
  }
  // 料金の段の図がある記事（scripts/article_charts.py が料金プランの記事だと判定したもの）
  const ladder = article.charts?.find((c) => c.kind === "ladder");
  if (ladder && ladder.kind === "ladder" && knownProducts.includes(ladder.product)) {
    return { kind: "ai-plan-cost", heading: PRICING_SECTION, product: ladder.product, plan: ladder.highlight };
  }
  // ドル建ての料金が判定カードに書いてある AI・テックの記事
  const usd = usdFromPrice(article.verdict?.price);
  if (usd !== null && (article.genreSlug === "ai" || article.genreSlug === "tech" || article.tags.includes("料金プラン"))) {
    return { kind: "usd-jpy", heading: PRICING_SECTION, usd };
  }
  return null;
}
