import { getPricingProduct } from "@/lib/pricing";

/**
 * AI の月額プランの段と為替（図・計算機の共通の読み口）。
 *
 * 元データは content/pricing/<製品>.json（lib/pricing.ts の getPricingProduct。scripts/pricing_watch.py が公式の料金ページと
 * ECB の参照レートから毎日作る）。読めないときは、下の「日付つきの控え」を使う。
 * 控えの値も公式ページ（claude.com/pricing・ECB）で確かめたもので、確認日を必ず表示する。
 */

export type Fx = { rate: number; date: string; source: string; fallback: boolean };

/** 為替の控え（content/pricing/_fx.json が無いとき）。2026-11-02 の ECB 参照レート（1ユーロ=1.1216ドル=177.10円）から計算 */
export const FX_FALLBACK: Fx = { rate: 157.9, date: "2026-11-02", source: "ECB参照レート（ユーロ経由で計算）", fallback: true };

export type PlanPrice = {
  /** 1か月あたりの金額 */
  amount: number;
  currency: "USD" | "JPY";
  billing: "monthly" | "annual";
  /** 年払いの一括請求額（分かるときだけ） */
  billedAs?: number;
};

export type Plan = {
  id: string;
  name: string;
  tier: string;
  prices: PlanPrice[];
  limits: string[];
};

export type PlanProduct = {
  product: string;
  name: string;
  checkedAt: string;
  sourceUrl: string;
  sourceTitle: string;
  taxNote?: string;
  /** 個人向けのプランだけ（チーム・法人は1人あたりの料金なので段に混ぜない） */
  plans: Plan[];
  fallback: boolean;
};

/** 控え：Claude の個人向けプラン（claude.com/pricing を 2026-11-02 に確認。content/howto/claude-pricing.json と同じ数字） */
const CLAUDE_FALLBACK: PlanProduct = {
  product: "claude",
  name: "Claude",
  checkedAt: "2026-11-02",
  sourceUrl: "https://claude.com/pricing",
  sourceTitle: "Claude 料金ページ",
  taxNote: "表示価格には適用される税金が含まれていません。",
  fallback: true,
  plans: [
    { id: "free", name: "Free", tier: "free", prices: [{ amount: 0, currency: "USD", billing: "monthly" }], limits: [] },
    {
      id: "pro",
      name: "Pro",
      tier: "standard",
      prices: [
        { amount: 20, currency: "USD", billing: "monthly" },
        { amount: 17, currency: "USD", billing: "annual", billedAs: 200 },
      ],
      limits: [],
    },
    { id: "max", name: "Max", tier: "high", prices: [{ amount: 100, currency: "USD", billing: "monthly" }], limits: ["Pro の 5 倍の使用量"] },
    { id: "ultra", name: "Ultra", tier: "top", prices: [{ amount: 200, currency: "USD", billing: "monthly" }], limits: ["Pro の 10 倍の使用量（毎週）"] },
  ],
};

type RawPrice = { amount?: number; currency?: string; per?: string; billing?: string | null; billedAs?: number };
type RawPlan = { id?: string; name?: string; tier?: string; audience?: string; prices?: RawPrice[]; limits?: string[] };
type RawProduct = {
  product?: string;
  name?: string;
  kind?: string;
  checkedAt?: string;
  sources?: { url?: string; title?: string }[];
  taxNote?: string;
  plans?: RawPlan[];
};

function toPrice(raw: RawPrice): PlanPrice | null {
  if (typeof raw.amount !== "number" || !Number.isFinite(raw.amount) || raw.amount < 0) return null;
  if (raw.currency !== "USD" && raw.currency !== "JPY") return null;
  // 「1か月あたり」だけ。年額表示（per: year）は12で割った値にせず、一括額として年払いに入れる
  if (raw.per === "month") {
    return { amount: raw.amount, currency: raw.currency, billing: raw.billing === "annual" ? "annual" : "monthly", ...(raw.billedAs ? { billedAs: raw.billedAs } : {}) };
  }
  if (raw.per === "year") {
    return { amount: Math.round((raw.amount / 12) * 100) / 100, currency: raw.currency, billing: "annual", billedAs: raw.amount };
  }
  return null;
}

function toProduct(raw: RawProduct, slug: string): PlanProduct | null {
  if (raw.kind !== "plan" || !Array.isArray(raw.plans)) return null;
  const plans: Plan[] = raw.plans
    .filter((p) => (p.audience ?? "individual") === "individual" && p.id && p.name)
    .map((p) => ({
      id: p.id!,
      name: p.name!,
      tier: p.tier ?? "",
      prices: (p.prices ?? []).map(toPrice).filter((x): x is PlanPrice => x !== null),
      limits: p.limits ?? [],
    }))
    .filter((p) => p.prices.length > 0);
  if (plans.length < 2) return null;
  const source = raw.sources?.[0];
  return {
    product: raw.product ?? slug,
    name: raw.name ?? slug,
    checkedAt: raw.checkedAt ?? "",
    sourceUrl: source?.url ?? "",
    sourceTitle: source?.title ?? `${raw.name ?? slug} 料金ページ`,
    taxNote: raw.taxNote,
    plans,
    fallback: false,
  };
}

const PLAN_PRODUCTS = ["claude", "chatgpt", "gemini", "copilot", "perplexity"] as const;

let cache: { products: PlanProduct[]; fx: Fx } | null = null;

function load(): { products: PlanProduct[]; fx: Fx } {
  if (cache) return cache;
  const products: PlanProduct[] = [];
  let rawFx: { rate?: number; date?: string; source?: string } | undefined;
  for (const id of PLAN_PRODUCTS) {
    const raw = getPricingProduct(id) as unknown as (RawProduct & { fx?: { rate?: number; date?: string; source?: string } }) | null;
    if (!raw) continue;
    rawFx ??= raw.fx;
    const product = toProduct(raw, id);
    if (product) products.push(product);
  }
  if (!products.some((p) => p.product === "claude")) products.unshift(CLAUDE_FALLBACK);
  const fx: Fx =
    rawFx && typeof rawFx.rate === "number" && rawFx.rate > 50 && rawFx.rate < 400 && rawFx.date
      ? { rate: rawFx.rate, date: rawFx.date, source: rawFx.source ?? "", fallback: false }
      : FX_FALLBACK;
  cache = { products: sortProducts(products), fx };
  return cache;
}

const ORDER = ["claude", "chatgpt", "gemini", "copilot", "perplexity"];
function sortProducts(list: PlanProduct[]): PlanProduct[] {
  const rank = (p: PlanProduct) => (ORDER.indexOf(p.product) < 0 ? 99 : ORDER.indexOf(p.product));
  return [...list].sort((a, b) => rank(a) - rank(b));
}

export function getFx(): Fx {
  return load().fx;
}

export function getPlanProducts(): PlanProduct[] {
  return load().products;
}

export function getPlanProduct(product: string): PlanProduct | undefined {
  return load().products.find((p) => p.product === product);
}
