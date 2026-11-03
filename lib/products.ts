import { articleTitle } from "@/lib/headline";
import fs from "@/lib/content-fs";
import path from "node:path";
import { getArticleSummary, type ArticleSummary } from "@/lib/articles";
import { withoutNoData } from "@/lib/no-data";
import sourcesJson from "@/content/product_sources.json";
import japanJson from "@/content/japan_availability.json";
import {
  getComparison,
  getLadder,
  getPricingProduct,
  type ApiTier,
  priceText,
  type ComparisonTable,
  type LadderPrice,
  type LadderTable,
  type ModelRow,
  type PlanRow,
  type PlanTier,
  type PricingProductId,
} from "@/lib/pricing-ladder";
import {
  cleanProductList,
  cleanProductText,
  officialPriceRowCount,
  politeNote,
  politeQuestion,
  productListing,
  productRelevance,
  type BrandImage,
  type Listing,
  type ProductSource,
} from "@/lib/product-substance";

/**
 * 製品ごとのまとめページ（content/products/*.json、scripts/products.py が作る）。
 *
 * ニュースは数日で読まれなくなるが、「〇〇とは」「料金」「日本で使える？」は長く検索される。
 * 中身はすべて自サイトの記事から取った事実で、項目ごとに出典の記事 slug を持つ。
 */
export type Sourced = { text: string; sources: string[] };

export type Product = {
  slug: string;
  name: string;
  aliases: string[];
  /** lib/companies.ts の slug（色・企業ページがない企業は "other"） */
  company: string;
  companyName: string;
  category: string;
  overview: Sourced;
  facts: { label: string; value: string; sources: string[] }[];
  capabilities: Sourced[];
  pricing: Sourced[];
  japan: Sourced[];
  howto: Sourced[];
  differences: Sourced[];
  faq: { q: string; a: string; sources: string[] }[];
  timeline: { date: string; slug: string; event: string }[];
  /** 関連する製品の slug */
  related: string[];
  /** 関係する記事（新しい順） */
  articles: string[];
  /** この製品のまとめページへ案内する記事 */
  linkFrom: string[];
  createdAt: string;
  updatedAt: string;
  officialCheck?: { date: string; url: string; label: string };
};

/** 自動再生成より優先する、公式資料で確認した編集部の訂正。 */
function editorialCorrections(product: Product): Product {
  if (product.slug === "meta-muse") {
    return {
      ...product,
      category: "アプリ・サービス",
      overview: { text: "Museは、買い物や旅行の下調べなどを手伝うMetaの個人向けAIエージェントです。", sources: [] },
      facts: [
        { label: "日本での提供", value: "2026年10月23日から提供", sources: [] },
        { label: "料金", value: "多くの基本機能は無料。有料プランもあります", sources: [] },
        { label: "利用環境", value: "iOS・Android・Web（muse.ai）", sources: [] },
      ],
      capabilities: [
        { text: "買い物や旅行の計画などを支援します。連携機能の提供状況はサービスごとに確認してください。", sources: [] },
        { text: "メール送信や購入などの重要な操作の前に確認する設計です。", sources: [] },
      ],
      pricing: [{ text: "日本では多くの基本機能が無料。有料プランもあり、料金・上限は公式画面で確認してください。", sources: [] }],
      japan: [{ text: "Metaは2026年10月23日から日本での提供を開始したと発表しています。国内企業との一部連携は進行中です。", sources: [] }],
      howto: [{ text: "iOS、Android、muse.aiで案内を確認し、接続先や権限を選びます。", sources: [] }],
      differences: [], faq: [],
      // 自動生成の日付は記事の取得日と発表日が混ざるため、元の発表日を確認して固定する。
      timeline: [
        { date: "2026-10-02", slug: "meta-muse-ai-agent-app-launch", event: "Museの紹介動画を取り上げた記事" },
        { date: "2026-10-26", slug: "nvidia-meta-muse-partnership-expansion", event: "NVIDIAとMetaがMuseの協業拡大を発表" },
        { date: "2026-10-27", slug: "meta-muse-singapore-launch", event: "Museがシンガポールで提供開始" },
      ],
      officialCheck: { date: "2026-10-31", url: "https://about.fb.com/ja/news/2026/10/muse-personal-ai-agent-launches-in-japan/", label: "Meta公式：日本での提供" },
    };
  }
  if (product.slug !== "openai-dot") return product;
  const source = "chatgpt-mobile-dot-setup";
  return {
    ...product,
    category: "アプリ・サービス",
    overview: { text: "OpenAIのdotは、ChatGPTで目標を伝えると、接続したアプリや専用の環境を使って調査や作業を進めるAIエージェントです。", sources: [source] },
    facts: [
      { label: "対象", value: "対象地域の18歳以上のPro、対象のBusiness Premium。Enterpriseなどは管理者によるベータ有効化が必要", sources: [] },
      { label: "料金", value: "最初の1体は対象プランに含まれます。より深い作業には利用枠があります", sources: [] },
      { label: "提供状況", value: "段階的に提供中。アカウントに届くまで数日かかる場合があります", sources: [] },
    ],
    capabilities: [
      { text: "接続を許可したアプリを使って、調査、資料作成、作業の整理などを進めます。", sources: [] },
      { text: "旅行手配のような用途もデモされています。購入や共有などの重要な操作は設定と確認条件を確かめてください。", sources: [] },
    ],
    pricing: [{ text: "最初のdotは対象プランに追加料金なしで含まれます。深い作業にはプランごとの利用枠があります。", sources: [] }],
    japan: [{ text: "提供対象はプラン、年齢、地域、組織の設定によって異なります。アカウントに表示される案内を確認してください。", sources: [] }],
    howto: [{ text: "対象アカウントではChatGPTのモバイルアプリ、デスクトップアプリ、デスクトップ版Webから設定を始められます。", sources: [] }],
    differences: [], faq: [],
    officialCheck: { date: "2026-10-31", url: "https://help.openai.com/en/articles/20001530-getting-started-with-your-dot", label: "OpenAI公式：dotの利用条件と設定" },
  };
}

// 文字列で組み立てて、content/ 全体をサーバー関数に同梱させない（lib/articles.ts と同じ理由）
const PRODUCTS_DIR = [process.cwd(), "content", "products"].join(path.sep);

let cache: Product[] | null = null;

/** 関係する記事の多い順 */
export function getAllProducts(): Product[] {
  if (cache) return cache;
  if (!fs.existsSync(PRODUCTS_DIR)) return (cache = []);
  cache = fs
    .readdirSync(PRODUCTS_DIR)
    .filter((file) => file.endsWith(".json"))
    .flatMap((file) => {
      try {
        const data = JSON.parse(fs.readFileSync(/* turbopackIgnore: true */ `${PRODUCTS_DIR}${path.sep}${file}`, "utf8")) as Product;
        // 基本情報の「記載なし」の欄は出さない（lib/no-data.ts）
        const facts = (data.facts ?? []).map((f) => ({ ...f, value: withoutNoData(f.value) })).filter((f) => f.value);
        return data.slug && data.overview?.text ? [editorialCorrections({ ...data, facts })] : [];
      } catch {
        return [];
      }
    })
    .sort((a, b) => b.articles.length - a.articles.length || a.name.localeCompare(b.name));
  return cache;
}

export function getProduct(slug: string): Product | undefined {
  return getAllProducts().find((p) => p.slug === slug);
}

let byArticle: Map<string, Product> | null = null;

/** 記事から案内するまとめページ（題名でいちばん手前に出てくる製品。1記事に1つ） */
export function productForArticle(articleSlug: string): Product | undefined {
  if (!byArticle) {
    byArticle = new Map();
    for (const product of listedProducts()) {
      for (const slug of product.linkFrom ?? []) if (!byArticle.has(slug)) byArticle.set(slug, product);
    }
  }
  return byArticle.get(articleSlug);
}

/** 企業ページに出す製品（関係する記事の多い順） */
export function productsByCompany(company: string): Product[] {
  return listedProducts().filter((p) => p.company === company);
}

/** 関係する記事（索引にある記事だけ。新しい順） */
export function productArticles(product: Product): ArticleSummary[] {
  return product.articles.map((slug) => getArticleSummary(slug)).filter((a): a is ArticleSummary => Boolean(a));
}

/** 料金・日本での提供がわかる製品か（題名の言い方を変える） */
export function hasBuyingInfo(product: Product): boolean {
  // 「未発表」だけの料金は、料金がわかる製品として扱わない（研究段階の製品など）
  return product.pricing.length > 0 || product.facts.some((f) => f.label === "料金" && !/未発表|未定|不明|なし/.test(f.value));
}

/** 検索結果の題名（決まった型の問いかけにしない。2026-11-03） */
export function productSeoTitle(product: Product): string {
  return productProfile(product).pricingTable
    ? `${product.name}とは｜料金プラン・機能・日本での提供状況`
    : `${product.name}とは｜機能と発表の履歴`;
}

/** ページの見出し */
export function productHeading(product: Product): string {
  return productProfile(product).pricingTable ? `${product.name}の料金・機能・日本での提供状況` : `${product.name}の機能と発表の履歴`;
}

/** 同じ製品の古い製品ガイド（/guides/<slug>）は、一覧に載るまとめページへ転送する（next.config.ts と同じ: 公式の料金がつながった製品だけ） */
export function supersededGuideSlugs(guideSlugs: string[]): string[] {
  return guideSlugs.filter(supersedesGuide);
}

// --------------------------------------------------------------------------- //
// 図鑑の中身（2026-11-03 オーナーの指摘「内容がすごく薄い」。台帳 W13）
// 確かめた公式データ（料金の段の表・日本での提供の表）と、自サイトの記事の日付つきの動きだけで組み立てる。
// --------------------------------------------------------------------------- //

const SOURCES = (sourcesJson as { items: Record<string, ProductSource> }).items;
const VENDORS = ((sourcesJson as { vendors?: Record<string, { logo?: BrandImage }> }).vendors ?? {}) as Record<string, { logo?: BrandImage }>;

/** 図鑑の一覧の提供元の見出しに出す公式ロゴ（無ければ企業の色見本） */
export function vendorLogo(company: string): BrandImage | undefined {
  return VENDORS[company]?.logo;
}

type JapanItem = { id: string; name: string; covers: string; japan: "ok" | "partial" | "no" | "unknown"; note: string; sources: { url: string; checkedAt: string }[] };
const JAPAN = (japanJson as { items: JapanItem[] }).items;

export const JAPAN_LABEL: Record<JapanItem["japan"], string> = { ok: "日本で利用可", partial: "条件付きで利用可", no: "日本では未提供", unknown: "" };

export type ProductJapan = { status: JapanItem["japan"]; label: string; covers: string; note: string; url: string; checkedAt: string };

export type ProductProfile = {
  product: Product;
  source: ProductSource | undefined;
  listing: Listing;
  overview: string;
  overviewSources: string[];
  audience: string;
  facts: Product["facts"];
  features: Sourced[];
  changes: Sourced[];
  howto: Sourced[];
  pricingTable: LadderTable | null;
  /** 一覧のカードに出す「〜から」 */
  startingPrice: string;
  startingPriceNote: string;
  rivals: ComparisonTable | null;
  japan: ProductJapan | null;
  timeline: { date: string; slug: string; event: string }[];
  faq: { q: string; a: string; sources: string[] }[];
  articles: ArticleSummary[];
  /** 題名に製品名が出てくる記事（「最新の関連記事」。少なければ関連記事全体） */
  latest: ArticleSummary[];
  relevance: number;
  updatedAt: string;
};

function pricingJson(): Record<string, { plans?: { id: string; prices?: unknown[] }[]; models?: { id: string; status?: string }[] } | undefined> {
  const out: Record<string, ReturnType<typeof getPricingProduct>> = {};
  for (const id of ["claude", "chatgpt", "gemini", "copilot", "perplexity", "claude-api", "openai-api", "gemini-api"] as PricingProductId[]) {
    out[id] = getPricingProduct(id);
  }
  return out as never;
}

/** つなぎの行だけに絞った料金の表（API は料金ページに載っている行だけ） */
function productPricingTable(product: Product, source: ProductSource | undefined): LadderTable | null {
  const link = source?.pricing;
  if (!link) return null;
  const ladder = getLadder(link.product as PricingProductId);
  if (!ladder) return null;
  const plans = new Set(link.plans ?? []);
  const models = new Set(link.models ?? []);
  const rows = ladder.rows.filter((r) => (r.kind === "plan" ? plans.has(r.id) : models.has(r.id) && (r as ModelRow).listed));
  if (rows.length === 0) return null;
  const used = new Set(rows.map((r) => r.sourceUrl));
  const sources = ladder.sources.filter((s) => used.has(s.url));
  return {
    ...ladder,
    title: ladder.kind === "api" ? `${product.name}の API 料金（100万トークンあたり）` : `${product.name}を使えるプラン`,
    sources: sources.length ? sources : ladder.sources,
    rows,
  };
}

/** いちばん安い公式の金額（プランのすべての払い方から。円に直して比べるだけで、表示は公式の金額のまま） */
function startingPriceOf(table: LadderTable | null, fx: number): { text: string; note: string } {
  if (!table) return { text: "", note: "" };
  if (table.kind === "api") {
    const row = (table.rows as ModelRow[]).find((r) => r.listed);
    return row ? { text: `入力${row.inputText}・出力${row.outputText}`, note: "100万トークンあたり" } : { text: "", note: "" };
  }
  const data = getPricingProduct(table.product);
  const ids = new Set(table.rows.map((r) => r.id));
  let best: { price: LadderPrice; plan: string; yen: number } | null = null;
  let free = false;
  for (const plan of data?.plans ?? []) {
    if (!ids.has(plan.id)) continue;
    if (plan.prices.some((p) => p.amount === 0)) free = true;
    for (const price of plan.prices) {
      if (typeof price.amount !== "number" || price.amount <= 0) continue;
      const yen = price.currency === "JPY" ? price.amount : price.amount * fx;
      if (!best || yen < best.yen) best = { price, plan: (table.rows as PlanRow[]).find((r) => r.id === plan.id)?.compareName ?? plan.name, yen };
    }
  }
  if (!best) return { text: "", note: "" };
  const text = priceText(best.price);
  // 無料プランがある製品は「無料プランあり」と、有料のいちばん安いプランを並べる
  return { text: /から/.test(text) ? text : text.replace(/^([^（]+)/, "$1から"), note: free ? `無料プランあり・有料は${best.plan}` : best.plan };
}

/** 近い他社の製品と、同じ段の料金で比べる表（公式の料金データがある段だけ。自社の行だけなら出さない） */
function rivalsOf(table: LadderTable | null, company: string, preferred?: string): ComparisonTable | null {
  if (!table) return null;
  const tier = preferred ?? (table.kind === "api" ? (table.rows[0] as ModelRow).tier : (table.rows[0] as PlanRow).tier);
  if (!tier) return null;
  const compare = getComparison(tier as PlanTier | ApiTier, table.kind);
  return compare.rows.some((r) => r.company !== company) ? compare : null;
}

function japanOf(source: ProductSource | undefined): ProductJapan | null {
  const item = source?.japan ? JAPAN.find((j) => j.id === source.japan) : undefined;
  if (!item || item.japan === "unknown") return null;
  const latest = [...item.sources].sort((a, b) => b.checkedAt.localeCompare(a.checkedAt))[0];
  return { status: item.japan, label: JAPAN_LABEL[item.japan], covers: item.covers, note: politeNote(item.note), url: latest?.url ?? "", checkedAt: latest?.checkedAt ?? "" };
}

/** 発表・更新の流れ（取り下げた記事は除く。記事の日付で並べる。無ければ関連記事の公開日で作る） */
function timelineOf(product: Product, articles: ArticleSummary[], skip: string[]) {
  const live = product.timeline.filter((t) => getArticleSummary(t.slug) && !skip.includes(t.slug));
  if (live.length >= 2) return [...live].sort((a, b) => b.date.localeCompare(a.date));
  const seen = new Set(live.map((t) => t.slug));
  const extra = articles
    .filter((a) => !seen.has(a.slug) && !skip.includes(a.slug))
    .slice(0, 6)
    .map((a) => ({ date: a.publishedAt.slice(0, 10), slug: a.slug, event: articleTitle(a) }));
  return [...live, ...extra].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8);
}

const squashName = (text: string) => text.normalize("NFKC").toLowerCase().replace(/[\s\-‐‑–—_・.]/g, "");

/** 題名に製品名・別名が出てくる記事（keywords だけで結び付いた記事を「最新の関連記事」に並べない） */
function titledArticles(product: Product, articles: ArticleSummary[]): ArticleSummary[] {
  const names = [product.name, ...(product.aliases ?? [])].map(squashName).filter((n) => n.length >= 3);
  const titled = articles.filter((a) => names.some((n) => squashName(`${a.title} ${a.seoTitle ?? ""}`).includes(n)));
  return (titled.length >= 3 ? titled : articles).slice(0, 8);
}

let profiles: Map<string, ProductProfile> | null = null;

/** 製品の図鑑ページの中身（一覧に出すかの判定つき） */
export function productProfile(product: Product): ProductProfile {
  if (!profiles) profiles = new Map();
  const hit = profiles.get(product.slug);
  if (hit) return hit;
  const source = SOURCES[product.slug];
  const articles = productArticles(product);
  const lead = cleanProductText(source?.lead);
  const overview = lead || cleanProductText(product.overview.text);
  const facts = product.facts
    .map((f) => ({ ...f, value: cleanProductText(f.value) }))
    // 料金・日本での提供は公式データの欄に出す（記事から拾った言い方と二重にしない）
    .filter((f) => f.value && !(source && (f.label === "料金" || f.label === "日本での提供")));
  const features = cleanProductList(product.capabilities);
  const changes = cleanProductList(product.differences);
  const howto = cleanProductList(product.howto);
  const pricingTable = productPricingTable(product, source);
  const fx = getPricingProduct("openai-api")?.fx?.rate ?? 150;
  const start = startingPriceOf(pricingTable, fx);
  const japan = japanOf(source);
  const timeline = timelineOf(product, articles, source?.timelineSkip ?? []);
  const faq = product.faq
    .map((f) => ({ ...f, q: politeQuestion(f.q), a: cleanProductText(f.a) }))
    // 「誰に向いている？」は「向いている人」の欄に出す（答えが短い型の問いを繰り返さない）
    .filter((f) => f.a.length >= 12 && !/誰に向いて|だれに向いて/.test(f.q));
  const audience = source?.audience ?? cleanProductText(product.facts.find((f) => f.label === "向いている人")?.value);
  const listing = productListing({
    overview,
    officialPriceRows: officialPriceRowCount(source, pricingJson()),
    datedEvents: timeline.filter((t) => getArticleSummary(t.slug)).length,
    liveArticles: articles.length,
    features: features.length + facts.length,
  });
  const dates = [product.updatedAt, pricingTable?.checkedAt ?? "", japan?.checkedAt ?? ""].filter(Boolean).sort();
  const profile: ProductProfile = {
    product,
    source,
    listing,
    overview,
    overviewSources: lead ? (source?.leadSources ?? []) : product.overview.sources,
    audience,
    facts,
    features,
    changes,
    howto,
    pricingTable,
    startingPrice: start.text,
    startingPriceNote: start.note,
    rivals: rivalsOf(pricingTable, product.company, source?.rivalsTier),
    japan,
    timeline,
    faq,
    articles,
    latest: titledArticles(product, articles),
    relevance: productRelevance(articles.map((a) => a.publishedAt), new Date()),
    updatedAt: dates[dates.length - 1],
  };
  profiles.set(product.slug, profile);
  return profile;
}

/** 図鑑の一覧に出す製品（中身がそろった製品だけ。関連度の高い順） */
export function listedProducts(): Product[] {
  return getAllProducts()
    .map(productProfile)
    .filter((p) => p.listing.listed)
    .sort((a, b) => b.relevance - a.relevance || a.product.name.localeCompare(b.product.name))
    .map((p) => p.product);
}

/**
 * 古い製品ガイド（/guides/<slug>）をまとめページへ移した製品か。公式の料金がつながった製品だけ（next.config.ts の転送と同じ判定）。
 * それ以外の製品のガイドは、ガイドのまま一覧に残す（中身の薄いまとめページへ転送しない）
 */
export function supersedesGuide(slug: string): boolean {
  return Boolean(SOURCES[slug]?.pricing && getProduct(slug));
}

export function isListedProduct(slug: string): boolean {
  const product = getProduct(slug);
  return Boolean(product && productProfile(product).listing.listed);
}
