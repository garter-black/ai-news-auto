import claude from "@/content/pricing/claude.json";
import chatgpt from "@/content/pricing/chatgpt.json";
import gemini from "@/content/pricing/gemini.json";
import copilot from "@/content/pricing/copilot.json";
import perplexity from "@/content/pricing/perplexity.json";
import claudeApi from "@/content/pricing/claude-api.json";
import openaiApi from "@/content/pricing/openai-api.json";
import geminiApi from "@/content/pricing/gemini-api.json";
import { getUsdJpy } from "@/lib/fx";
import { withoutNoData, withoutNoDataList } from "@/lib/no-data";

/**
 * 料金の段の表と、会社をまたいだ比較表（content/pricing/<product>.json）。
 * データは scripts/pricing_watch.py が公式の料金ページから毎日作り直す。金額はすべて公式ページの表記。
 * 円の目安（為替・消費税）だけは計算した値で、必ず「目安」と表示する。Python 版は scripts/pricing.py（同じ考え方）。
 *
 *   getLadder("claude")                 // Claude の Free〜Ultra・Team の段の表
 *   getComparison("standard")           // 月20ドル前後のプランを会社をまたいで比べる
 *   getComparison("flagship", "api")    // 各社の最上位モデルの API 料金
 *   getCompanyLadders("google")         // 1社の製品すべて
 *   findPricingProducts(text)           // 文章に出てくる製品
 */

export type PlanProductId = "claude" | "chatgpt" | "gemini" | "copilot" | "perplexity";
export type ApiProductId = "claude-api" | "openai-api" | "gemini-api";
export type PricingProductId = PlanProductId | ApiProductId;
export type PlanTier = "free" | "low" | "standard" | "high" | "top" | "team" | "enterprise";
export type ApiTier = "flagship" | "mid" | "small";

export type YenEstimate = {
  exTax: number;
  inclTax: number | null;
  rate: number | null;
  rateDate: string | null;
  rateSource: string | null;
  estimate: true;
};

export type LadderPrice = {
  amount: number;
  currency: "USD" | "JPY";
  per?: "month" | "seat-month" | "year" | "seat-year";
  billing?: "monthly" | "annual";
  billedAs?: number;
  regular?: number;
  from?: boolean;
  taxIncluded?: boolean | null;
  src?: "auto" | "manual";
  label?: string;
  /** この金額を読んだページ（手入力の円の価格など、表の出典と違うとき） */
  sourceUrl?: string;
  jpy?: YenEstimate;
};

export type LadderPlan = {
  id: string;
  name: string;
  tier?: PlanTier;
  audience?: string;
  prices: LadderPrice[];
  limits?: string[];
  note?: string;
  sourceUrl?: string;
};

export type LadderModel = {
  id: string;
  name: string;
  tier?: string | null;
  class?: ApiTier | null;
  primary?: boolean;
  status?: "listed" | "not_listed";
  currency?: "USD";
  input?: number;
  output?: number;
  cacheRead?: number;
  scheduled?: { from: string; input?: number; output?: number; cacheRead?: number }[];
  notes?: string[];
  sourceUrl?: string;
  jpy?: { input?: number; output?: number; cacheRead?: number; rate: number; rateDate: string; rateSource: string };
};

export type FxRate = { base: string; quote: string; rate: number; date: string; source: string; sourceUrl?: string };

export type PricingSource = { url: string; title: string; checkedAt: string; how?: "auto" | "manual" };

export type PricingProduct = {
  product: PricingProductId;
  name: string;
  company: string;
  kind: "plan" | "api";
  mode: "auto" | "manual" | "mixed";
  checkedAt: string;
  updatedAt: string;
  sources: PricingSource[];
  taxNote?: string;
  notes?: string[];
  fx?: FxRate;
  plans?: LadderPlan[];
  models?: LadderModel[];
};

/** 表の1行（プラン） */
export type PlanRow = {
  kind: "plan";
  product: PricingProductId;
  productName: string;
  /** 比較表での名前（「Claude Pro」「Google AI Pro」） */
  compareName: string;
  company: string;
  id: string;
  name: string;
  tier: PlanTier | null;
  priceTexts: string[];
  mainPriceText: string;
  jpyText: string;
  limits: string[];
  manual: boolean;
  sourceUrl: string;
};

/** 表の1行（API モデル） */
export type ModelRow = {
  kind: "api";
  product: PricingProductId;
  productName: string;
  company: string;
  id: string;
  name: string;
  context: string | null;
  tier: ApiTier | null;
  listed: boolean;
  inputText: string;
  outputText: string;
  cacheReadText: string;
  jpyText: string;
  scheduledText: string;
  notes: string[];
  sourceUrl: string;
};

export type LadderTable = {
  kind: "plan" | "api";
  title: string;
  product: PricingProductId;
  company: string;
  mode: PricingProduct["mode"];
  checkedAt: string;
  sources: PricingSource[];
  taxNote: string;
  fxNote: string;
  notes: string[];
  rows: (PlanRow | ModelRow)[];
};

export type ComparisonTable = {
  kind: "plan" | "api";
  title: string;
  tier: string;
  checkedAt: string;
  sources: PricingSource[];
  fxNote: string;
  notes: string[];
  rows: (PlanRow | ModelRow)[];
};

export const PLAN_PRODUCTS: PlanProductId[] = ["claude", "chatgpt", "gemini", "copilot", "perplexity"];
export const API_PRODUCTS: ApiProductId[] = ["claude-api", "openai-api", "gemini-api"];

export const PLAN_TIER_LABELS: Record<PlanTier, string> = {
  free: "無料",
  low: "お手ごろ（月1,000〜2,000円前後）",
  standard: "標準（月20ドル・3,000円前後）",
  high: "上位（月100ドル前後）",
  top: "最上位（月200ドル以上）",
  team: "チーム・法人（1人あたり）",
  enterprise: "大企業向け",
};
export const API_TIER_LABELS: Record<ApiTier, string> = { flagship: "最上位モデル", mid: "中くらいのモデル", small: "小型・低価格モデル" };

const DATA: Record<PricingProductId, PricingProduct> = {
  claude: claude as unknown as PricingProduct,
  chatgpt: chatgpt as unknown as PricingProduct,
  gemini: gemini as unknown as PricingProduct,
  copilot: copilot as unknown as PricingProduct,
  perplexity: perplexity as unknown as PricingProduct,
  "claude-api": claudeApi as unknown as PricingProduct,
  "openai-api": openaiApi as unknown as PricingProduct,
  "gemini-api": geminiApi as unknown as PricingProduct,
};

/** 比較表でプラン名の前に付ける製品名（プラン名に会社名が入っているものは付けない） */
const SHORT: Record<PricingProductId, string> = {
  claude: "Claude",
  chatgpt: "ChatGPT",
  gemini: "",
  copilot: "",
  perplexity: "Perplexity",
  "claude-api": "",
  "openai-api": "",
  "gemini-api": "",
};

const ALIASES: Record<PricingProductId, string[]> = {
  claude: ["Claude", "クロード", "Claude Pro", "Claude Max", "Claude Ultra", "Claude Team"],
  chatgpt: ["ChatGPT", "チャットGPT", "ChatGPT Plus", "ChatGPT Pro", "ChatGPT Go", "ChatGPT Business"],
  gemini: ["Gemini", "ジェミニ", "Google AI Pro", "Google AI Ultra", "Google AI Plus"],
  copilot: ["Copilot", "コパイロット", "Microsoft 365"],
  perplexity: ["Perplexity", "パープレキシティ"],
  "claude-api": ["Claude API", "Anthropic API", "Claude Platform", "Opus 5.5", "Sonnet 5.5", "Haiku 5.5"],
  "openai-api": ["OpenAI API", "GPT-6 Astra", "GPT-6.1 Sol", "GPT-6.1 Terra", "gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra"],
  "gemini-api": ["Gemini API", "Gemini 4 Argon", "Gemini 4 Carbon", "Gemini 3.8 Flash", "AI Studio"],
};

// --------------------------------------------------------------------------- //
// 表示の文字（scripts/pricing.py と同じ書き方）
// --------------------------------------------------------------------------- //

export function money(amount: number, currency: "USD" | "JPY"): string {
  if (currency === "JPY") return `${Math.round(amount).toLocaleString("ja-JP")}円`;
  if (amount === 0) return "$0";
  if (amount >= 1 && Number.isInteger(amount)) return `$${amount.toLocaleString("en-US")}`;
  // $0.125 は3桁、$0.75・$2.50 は2桁（公式ページの書き方に合わせる）
  const text = amount.toLocaleString("en-US", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  return `$${text.endsWith("0") ? text.slice(0, -1) : text}`;
}

export function yenText(value: number): string {
  if (value < 1) return "1円未満";
  if (value < 10) return `約${Number(value.toFixed(1))}円`;
  if (value < 100) return `約${Math.round(value)}円`;
  return `約${(Math.round(value / 10) * 10).toLocaleString("ja-JP")}円`;
}

const UNIT: Record<string, string> = { month: "/月", "seat-month": "/人・月", year: "/年", "seat-year": "/人・年" };
const BILLING: Record<string, string> = { monthly: "月払い", annual: "年払い" };

export function priceText(price: LadderPrice): string {
  const head = money(price.amount, price.currency) + (UNIT[price.per ?? "month"] ?? "") + (price.from ? "から" : "");
  const extra: string[] = [];
  if (price.billing) extra.push(BILLING[price.billing]);
  if (price.billedAs) extra.push(`一括${money(price.billedAs, price.currency)}`);
  if (price.taxIncluded === false && price.currency === "JPY") extra.push("税別");
  if (price.regular && price.regular !== price.amount) extra.push(`通常${money(price.regular, price.currency)}`);
  return extra.length ? `${head}（${extra.join("・")}）` : head;
}

export function jpyText(price: LadderPrice | null | undefined): string {
  const est = price?.jpy;
  if (!est || price?.amount === 0) return "";
  if (est.inclTax != null) return `${yenText(est.inclTax)}（税込みの目安）`;
  return `${yenText(est.exTax)}（税別の目安）`;
}

/** 円の目安の注記。為替はサイト共通の1つ（lib/fx.ts）を出す（製品ごとのファイルの fx が古くても、表示はサイトで1つにそろえる） */
export function fxNote(_fx: FxRate | undefined, kind: "plan" | "api" = "plan"): string {
  const fx = getUsdJpy();
  if (!(fx.rate > 50 && fx.rate < 400)) return "";
  const tax = kind === "api" ? "API の料金は税別です。" : "ドルのプランは消費税10%を足しています。";
  return `円は1ドル=${fx.rate.toFixed(2)}円（${fx.date}、${fx.source}）で計算した目安です。${tax}実際の請求額は為替とカード会社の手数料で変わります。`;
}

/** 表の1行に出す代表の金額。円の公式価格 → 月払い → 最初の順 */
function mainPrice(plan: LadderPlan): LadderPrice | null {
  const prices = plan.prices.filter((p) => typeof p.amount === "number");
  const picks: ((p: LadderPrice) => boolean)[] = [
    (p) => p.currency === "JPY" && p.billing !== "annual",
    (p) => p.currency === "JPY",
    (p) => p.billing === "monthly",
  ];
  for (const pick of picks) {
    const found = prices.find(pick);
    if (found) return found;
  }
  return prices[0] ?? null;
}

/** 円の目安を出す金額（ドルの価格のうち代表のもの） */
function estimatePrice(plan: LadderPlan, main: LadderPrice | null): LadderPrice | null {
  if (main?.jpy) return main;
  return plan.prices.find((p) => p.jpy && p.billing !== "annual") ?? plan.prices.find((p) => p.jpy) ?? null;
}

function planRow(data: PricingProduct, plan: LadderPlan): PlanRow {
  const main = mainPrice(plan);
  return {
    kind: "plan",
    product: data.product,
    productName: data.name,
    compareName: SHORT[data.product] ? `${SHORT[data.product]} ${plan.name}` : plan.name,
    company: data.company,
    id: plan.id,
    name: plan.name,
    tier: plan.tier ?? null,
    priceTexts: plan.prices.map(priceText),
    mainPriceText: main ? priceText(main) : "",
    // 円の公式価格があるときは目安を出さない（公式の円が正）
    jpyText: main?.currency === "JPY" && main.taxIncluded !== false ? "" : jpyText(estimatePrice(plan, main)),
    limits: withoutNoDataList(plan.limits),
    manual: plan.prices.some((p) => p.src === "manual"),
    sourceUrl: main?.sourceUrl ?? plan.sourceUrl ?? data.sources[0]?.url ?? "",
  };
}

function modelRow(data: PricingProduct, model: LadderModel): ModelRow {
  const cell = (v: number | undefined) => (typeof v === "number" ? money(v, "USD") : "—");
  const jp = model.jpy;
  const scheduled = (model.scheduled ?? [])
    .map((s) => {
      const parts = [s.input != null ? `入力${money(s.input, "USD")}` : "", s.output != null ? `出力${money(s.output, "USD")}` : ""].filter(Boolean);
      const [y, m, d] = s.from.split("-").map(Number);
      return parts.length ? `${y}年${m}月${d}日から ${parts.join("・")}` : "";
    })
    .filter(Boolean)
    .join("、");
  return {
    kind: "api",
    product: data.product,
    productName: data.name,
    company: data.company,
    id: model.id,
    name: model.name,
    context: model.tier ?? null,
    tier: model.class ?? null,
    listed: model.status !== "not_listed",
    inputText: cell(model.input),
    outputText: cell(model.output),
    cacheReadText: cell(model.cacheRead),
    jpyText: jp?.input != null && jp?.output != null ? `${yenText(jp.input)}／${yenText(jp.output)}` : "",
    scheduledText: scheduled,
    notes: withoutNoDataList(model.notes),
    sourceUrl: model.sourceUrl ?? data.sources[0]?.url ?? "",
  };
}

// --------------------------------------------------------------------------- //
// 段の表・比較表
// --------------------------------------------------------------------------- //

export function getPricingProduct(id: PricingProductId): PricingProduct | null {
  return DATA[id] ?? null;
}

export function getLadder(id: PricingProductId): LadderTable | null {
  const data = DATA[id];
  if (!data) return null;
  const rows = data.kind === "api" ? (data.models ?? []).map((m) => modelRow(data, m)) : (data.plans ?? []).map((p) => planRow(data, p));
  return {
    kind: data.kind,
    title: data.kind === "api" ? `${data.name}の料金（100万トークンあたり）` : `${data.name}の料金プラン`,
    product: data.product,
    company: data.company,
    mode: data.mode,
    checkedAt: data.checkedAt,
    sources: data.sources,
    // 「税の扱いの記載なし」のような文は出さない（読者に「記載なし」を見せない。lib/no-data.ts）
    taxNote: withoutNoData(data.taxNote),
    fxNote: fxNote(data.fx, data.kind),
    notes: withoutNoDataList(data.notes),
    rows,
  };
}

export function getComparison(tier: PlanTier | ApiTier, kind: "plan" | "api" = "plan"): ComparisonTable {
  const rows: (PlanRow | ModelRow)[] = [];
  const sources: PricingSource[] = [];
  const checked: string[] = [];
  let fx: FxRate | undefined;
  for (const id of kind === "plan" ? PLAN_PRODUCTS : API_PRODUCTS) {
    const data = DATA[id];
    const picked =
      kind === "plan"
        ? (data.plans ?? []).filter((p) => p.tier === tier).map((p) => planRow(data, p))
        : (data.models ?? []).filter((m) => m.class === tier && m.primary !== false && m.status !== "not_listed").map((m) => modelRow(data, m));
    if (!picked.length) continue;
    rows.push(...picked);
    // 出典は表に出した行の分だけ
    const used = new Set(picked.map((r) => r.sourceUrl));
    sources.push(...data.sources.filter((src) => used.has(src.url)));
    checked.push(data.checkedAt);
    fx ??= data.fx;
  }
  const label = kind === "plan" ? PLAN_TIER_LABELS[tier as PlanTier] : API_TIER_LABELS[tier as ApiTier];
  return {
    kind,
    title: kind === "plan" ? `${label}のプランを比べる` : `${label}の API 料金を比べる（100万トークンあたり）`,
    tier,
    checkedAt: checked.sort()[0] ?? "",
    sources,
    fxNote: fxNote(fx, kind),
    notes: ["段・クラスの分け方はサイソク編集部の目安です（公式の区分ではありません）。"],
    rows,
  };
}

/** 1社の製品の段の表すべて（例: google → Gemini のプランと Gemini API） */
export function getCompanyLadders(company: string): LadderTable[] {
  return [...PLAN_PRODUCTS, ...API_PRODUCTS]
    .filter((id) => DATA[id].company === company)
    .map((id) => getLadder(id))
    .filter((t): t is LadderTable => t !== null);
}

const squash = (text: string) => text.normalize("NFKC").toLowerCase().replace(/[\s\-‐‑–—_・]/g, "");

/** 文章に出てくる製品 */
export function findPricingProducts(text: string): PricingProductId[] {
  const flat = squash(text);
  return [...PLAN_PRODUCTS, ...API_PRODUCTS].filter((id) => ALIASES[id].some((a) => flat.includes(squash(a))));
}
