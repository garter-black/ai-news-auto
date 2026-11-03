import japanJson from "@/content/japan_availability.json";
import {
  API_PRODUCTS,
  getPricingProduct,
  PLAN_PRODUCTS,
  type LadderPlan,
  type LadderPrice,
  type PricingProduct,
} from "@/lib/pricing-ladder";
import type { PriceItem } from "@/lib/pricing";

/**
 * AI料金表（/ai-pricing）の行を、各社の公式の料金ページから作る（2026-11-03 オーナー決定・台帳 W21）。
 *
 * 元データは content/pricing/<製品>.json（scripts/pricing_watch.py が毎朝、公式の料金ページを読み直して作る。出典の URL と確認日つき）。
 * 前の早見表は「サイソクの記事に書かれた料金」（content/pricing.json）を出典にしていたが、自社の記事は公式の出典にならないので使わない。
 * 公式の料金ページで確かめられない製品（料金ページが無い・読めない）は載せない。
 * 日本で使えるかは、人が確かめた表 content/japan_availability.json（出典と確認日つき）だけで決める（記事の判定は使わない）。
 */

type Availability = { id: string; match: string; japan: "ok" | "partial" | "no" | "unknown" };
const AVAILABILITY: { re: RegExp; japan: Availability["japan"] }[] = ((japanJson as { items?: Availability[] }).items ?? [])
  .map((item) => {
    try {
      return { re: new RegExp(item.match), japan: item.japan };
    } catch {
      return null;
    }
  })
  .filter((x): x is { re: RegExp; japan: Availability["japan"] } => x !== null);

/** 日本での提供（確認表の最初に当たった行。当たらなければ未確認） */
export function japanOf(text: string): PriceItem["japan"] {
  return AVAILABILITY.find((a) => a.re.test(text))?.japan ?? "unknown";
}

/** 会社の表示名（lib/companies.ts に無い会社だけに使う。「Perplexity」） */
const vendorOf = (data: PricingProduct) => data.name.replace(/（.*?）/g, "").replace(/ API$/, "").trim();

function sourceOf(data: PricingProduct, url: string | undefined) {
  const found = (url && data.sources.find((s) => s.url === url)) || data.sources[0];
  return { url: found?.url ?? url ?? "", title: found?.title ?? `${data.name} 料金ページ`, checkedAt: found?.checkedAt || data.checkedAt };
}

/** 1か月あたりの代表の金額。円の公式価格 → ドルの月払い → 年払い（1か月あたり）の順 */
function monthlyPrice(plan: LadderPlan): { price: LadderPrice; annual: boolean } | null {
  const monthly = plan.prices.filter((p) => p.per === "month" && p.amount > 0);
  const pick =
    monthly.find((p) => p.currency === "JPY" && p.billing !== "annual") ??
    monthly.find((p) => p.currency === "USD" && p.billing !== "annual") ??
    monthly.find((p) => p.billing === "annual");
  if (pick) return { price: pick, annual: pick.billing === "annual" };
  const yearly = plan.prices.find((p) => p.per === "year" && p.amount > 0);
  return yearly ? { price: { ...yearly, amount: Math.round((yearly.amount / 12) * 100) / 100 }, annual: true } : null;
}

function planItems(data: PricingProduct): PriceItem[] {
  const hasFree = (data.plans ?? []).some((p) => (p.audience ?? "individual") === "individual" && p.prices.some((x) => x.amount === 0));
  const out: PriceItem[] = [];
  for (const plan of data.plans ?? []) {
    if ((plan.audience ?? "individual") !== "individual") continue;
    const picked = monthlyPrice(plan);
    if (!picked) continue;
    const src = sourceOf(data, picked.price.sourceUrl ?? plan.sourceUrl);
    // 「Microsoft 365 Personal」のように、プラン名に製品名が入っているときは製品名を重ねない
    const short = data.name.replace(/（.*?）/g, "").trim();
    const inName = plan.name.startsWith(short) || /^(Microsoft 365|Google AI)/.test(plan.name);
    const suffix = picked.annual ? "（年払い）" : "";
    out.push({
      id: `plan-${data.product}-${plan.id}`,
      kind: "plan",
      vendor: vendorOf(data),
      companySlug: data.company,
      product: inName ? `${plan.name}${suffix}` : short,
      plan: inName ? null : `${plan.name}${suffix}`,
      currency: picked.price.currency,
      monthly: picked.price.amount,
      input: null,
      output: null,
      cachedInput: null,
      japan: japanOf(`${data.name} ${plan.name}`),
      status: "available",
      free: hasFree ? true : null,
      legacy: false,
      source: { slug: "", title: src.title, publishedAt: src.checkedAt, quote: picked.price.label ?? "", url: src.url },
      verdictSource: null,
      confirmedIn: 1,
    });
  }
  return out;
}

function apiItems(data: PricingProduct): PriceItem[] {
  const seen = new Set<string>();
  const out: PriceItem[] = [];
  for (const model of data.models ?? []) {
    // 料金ページに載っていないモデル・長い入力の2段目（primary: false）は行にしない
    if (model.status === "not_listed" || model.primary === false || seen.has(model.id)) continue;
    if (typeof model.input !== "number" && typeof model.output !== "number") continue;
    seen.add(model.id);
    const src = sourceOf(data, model.sourceUrl);
    out.push({
      id: `api-${model.id}`,
      kind: "api",
      vendor: vendorOf(data),
      companySlug: data.company,
      product: model.tier ? `${model.name}（${model.tier}）` : model.name,
      plan: null,
      currency: "USD",
      monthly: null,
      input: typeof model.input === "number" ? model.input : null,
      output: typeof model.output === "number" ? model.output : null,
      cachedInput: typeof model.cacheRead === "number" ? model.cacheRead : null,
      japan: japanOf(`${data.name} ${model.name}`),
      status: "available",
      free: null,
      legacy: false,
      source: { slug: "", title: src.title, publishedAt: src.checkedAt, quote: "", url: src.url },
      verdictSource: null,
      confirmedIn: 1,
    });
  }
  return out;
}

/** 早見表の行（API と個人向けの月額プラン）。すべて公式の料金ページの金額 */
export function officialPriceItems(): PriceItem[] {
  const items: PriceItem[] = [];
  for (const id of API_PRODUCTS) {
    const data = getPricingProduct(id);
    if (data) items.push(...apiItems(data));
  }
  for (const id of PLAN_PRODUCTS) {
    const data = getPricingProduct(id);
    if (data) items.push(...planItems(data));
  }
  return items;
}

/** 早見表の確認日（行に使った公式ページのうち、いちばん新しい確認日。YYYY-MM-DD）。トップの案内も同じ値を出す */
export function officialPricingCheckedAt(): string {
  const dates = officialPriceItems().map((i) => i.source.publishedAt).filter(Boolean).sort();
  return dates.at(-1) ?? "";
}

/** 早見表に使った公式ページ（会社ごと・重複なし） */
export function officialPricingSources(): { company: string; title: string; url: string; checkedAt: string }[] {
  const out = new Map<string, { company: string; title: string; url: string; checkedAt: string }>();
  for (const item of officialPriceItems()) {
    if (item.source.url && !out.has(item.source.url)) {
      out.set(item.source.url, { company: item.companySlug, title: item.source.title, url: item.source.url, checkedAt: item.source.publishedAt });
    }
  }
  return [...out.values()];
}
