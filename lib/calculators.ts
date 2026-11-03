import type { ArticleChart } from "@/lib/charts";

/**
 * 記事に合う計算機（1記事に1つだけ）。本文の合う章の下に置き、無ければ本文の終わりに置く。
 * 計算機そのものは /tools/* の単独ページと同じ部品（components/calc）。
 */
export type CalculatorKind = "usd-jpy" | "defense-tax";

export type ArticleCalculator = {
  kind: CalculatorKind;
  /** この見出しの章の下に置く（無ければ本文の終わり） */
  heading: RegExp | null;
  /** ドル→円の初期の金額 */
  usd?: number;
};

export const CALCULATORS: Record<CalculatorKind, { href: string; title: string; description: string }> = {
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
  // 料金の段の図がある記事（scripts/article_charts.py が料金プランの記事だと判定したもの）は、図に円の目安があるので置かない
  if (article.charts?.some((c) => c.kind === "ladder")) return null;
  // ドル建ての料金が判定カードに書いてある AI・テックの記事
  const usd = usdFromPrice(article.verdict?.price);
  if (usd !== null && (article.genreSlug === "ai" || article.genreSlug === "tech" || article.tags.includes("料金プラン"))) {
    return { kind: "usd-jpy", heading: PRICING_SECTION, usd };
  }
  return null;
}
