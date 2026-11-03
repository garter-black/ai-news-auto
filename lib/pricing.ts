import data from "@/content/pricing.json";
import { getCompany, OTHER } from "@/lib/companies";
import { officialPriceItems } from "@/lib/official-pricing";
import { listedProducts } from "@/lib/products";

/**
 * AI料金・日本対応の早見表（/ai-pricing）のデータ。scripts/pricing_table.py が作る content/pricing.json。
 * 数字はすべてサイソクの記事に書かれたもので、記事本文との一致を機械的に確かめてある。
 * JSON は小さいのでページに直接読み込む（Cloudflare の仮想 fs を通さない）。
 */
export type PriceItem = {
  id: string;
  kind: "plan" | "api";
  vendor: string;
  companySlug: string;
  product: string;
  plan: string | null;
  currency: "USD" | "JPY";
  /** 月額（plan） */
  monthly: number | null;
  /** 100万トークンあたり（api） */
  input: number | null;
  output: number | null;
  cachedInput: number | null;
  japan: "ok" | "partial" | "no" | "unknown";
  status: "available" | "limited" | "soon" | "demo" | null;
  free: boolean | null;
  /** 同じ系列に新しい版がある API モデル */
  legacy: boolean;
  /** 出典。公式の料金ページの行（lib/official-pricing.ts）は url と確認日（publishedAt）を持ち、slug は空 */
  source: { slug: string; title: string; publishedAt: string; quote: string; url?: string };
  /** 日本対応の判定を別の記事から取ったとき、その記事 */
  verdictSource: string | null;
  /** この数字が書かれていた記事の数 */
  confirmedIn: number;
};

export type PricingData = {
  updatedAt: string;
  generatedAt: string;
  stats: {
    articlesScanned: number;
    candidateArticles: number;
    rowsVerified: number;
    rowsRejected: number;
    plans: number;
    apis: number;
  };
  items: PriceItem[];
};

/** 画面に渡す形（企業の色・名前・製品まとめページつき。クライアント部品に渡せる値だけ） */
export type PriceRow = PriceItem & { color: string; companyName: string; mark: string; productSlug: string | null };

const squash = (text: string) => text.normalize("NFKC").toLowerCase().replace(/[\s\-‐‑–—_・]/g, "");

/** 製品名が製品まとめページ（/products）の名前・別名と一致すれば、その slug */
function productSlugOf(name: string): string | null {
  const key = squash(name);
  const found = listedProducts().find((p) => [p.name, ...(p.aliases ?? [])].some((n) => squash(n) === key));
  return found?.slug ?? null;
}

const pricing = data as unknown as PricingData;

export function getPricing(): PricingData {
  return pricing;
}

/** 記事から抜き出した数字の行（content/pricing.json）。早見表のページはもう使わない（公式の料金ページの行は officialPriceRows） */
export function priceRows(items: PriceItem[] = pricing.items): PriceRow[] {
  return items.map((item) => {
    const company = getCompany(item.companySlug);
    const known = company && company.slug !== OTHER.slug;
    return {
      ...item,
      color: known ? company.color : OTHER.color,
      companyName: known ? company.name : item.vendor || OTHER.name,
      // 会社の目印の文字（色見本と同じ。知らない会社は名前の頭文字）
      mark: known ? company.mark : (item.vendor || "?").slice(0, 1).toUpperCase(),
      productSlug: item.kind === "api" ? productSlugOf(item.product) : null,
    };
  });
}

/** 早見表（/ai-pricing）の行。各社の公式の料金ページの金額だけ（lib/official-pricing.ts。台帳 W21） */
export function officialPriceRows(): PriceRow[] {
  return priceRows(officialPriceItems());
}

/** 記事が早見表の出典になっているか（記事ページから早見表へ案内するため） */
export function isPricingSource(slug: string): boolean {
  return pricing.items.some((item) => item.source.slug === slug || item.verdictSource === slug);
}

export { formatMoney, formatYen } from "@/lib/money";

/**
 * 公式の料金ページから毎日作り直す「料金の段の表・会社をまたいだ比較表」（content/pricing/<product>.json）。
 * 上の早見表（記事から抜き出した数字）とは別のデータ。生成・ページからはここから読む。
 */
export {
  getLadder,
  getComparison,
  getCompanyLadders,
  getPricingProduct,
  findPricingProducts,
  PLAN_TIER_LABELS,
  API_TIER_LABELS,
} from "@/lib/pricing-ladder";
export type {
  LadderTable,
  ComparisonTable,
  PlanRow,
  ModelRow,
  PricingProductId,
  PlanTier,
  ApiTier,
} from "@/lib/pricing-ladder";
