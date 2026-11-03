import fs from "@/lib/content-fs";
import path from "node:path";
import { getArticleSummary, type ArticleSummary } from "@/lib/articles";
import { partnerItem, type OfferItem } from "@/lib/offers";
import { withoutNoData, withoutNoDataList } from "@/lib/no-data";

/**
 * 比較ガイド（content/buying-guides/*.json）。scripts/buying_guides.py が作る。
 * 商品名・画像・リンクは offers.json と affiliate_links.json から毎回引く（提携が切れた商品は自動で消える）。
 */

export type PriceSource =
  | { type: "official"; url: string; checkedAt: string }
  | { type: "data"; file: string; checkedAt: string }
  | null;

/** サイソク判定の段階（公式の記載から機械的に決める。点数ではない） */
export type VerdictLevel = "yes" | "partial" | "no";

export type BuyingGuideProduct = {
  /** 提携商品は offers.json の partners のキー。提携していない商品（公式サイトへ nofollow で案内）は省略 */
  partner?: string;
  /** 新形式（schemaVersion 2）の識別子。省略時は partner を使う */
  id?: string;
  /** 提携していない商品の表示名（提携商品は offers.json の name を使う） */
  name?: string;
  maker?: string;
  /** 写真の代わりに出す種別（例: iPhone標準アプリ） */
  category?: string;
  price: string;
  priceSource: PriceSource;
  priceNote?: string;
  /** 旧形式の比較表の列（columns と同じ順）。新形式では cells を使う */
  specs?: string[];
  bestFor: string;
  summary: string;
  strengths: string[];
  limits: string[];
  officialUrl?: string;
  /** 以下は新形式のみ */
  verdict?: {
    japan: { level: VerdictLevel; text: string };
    freeTrial: { level: VerdictLevel; text: string };
    cost: string;
    bestFor: string;
  };
  /** 比較表の値（rows の key ごと） */
  cells?: Record<string, string>;
  /** 診断チャートの絞り込みに使う当てはまり（quiz の key → value の一覧） */
  fit?: Record<string, string[]>;
  sources?: { label: string; url: string }[];
};

export type BuyingGuide = {
  /** 2 = 提携外の商品も含めて手で作った新形式 */
  schemaVersion?: number;
  slug: string;
  title: string;
  /** 「〇〇を選ぶなら」に使う短い名前 */
  shortName?: string;
  description: string;
  targetQueries: string[];
  /** offers.json の話題キー。記事ページから案内するのに使う */
  topics: string[];
  intro: string;
  criteria: string[];
  columns: string[];
  products: BuyingGuideProduct[];
  quickPick: { partner: string; reason: string }[];
  faq: { q: string; a: string }[];
  relatedArticleSlugs: string[];
  publishedAt: string;
  updatedAt: string;
  priceCheckedAt: string;
  /** 以下は新形式のみ */
  /** 価格・仕様の時点と出典の表記（例: 10月27日時点・公式サイトより） */
  checkedLabel?: string;
  personas?: { who: string; picks: string[]; why: string }[];
  rows?: { key: string; label: string }[];
  decisionPoints?: { title: string; body: string }[];
  quiz?: { key: string; label: string; options: { value: string; label: string }[] }[];
  howWeMade?: string;
  editorial?: "manual";
};

/** 表示用：ガイドの商品に提携リンクを合わせたもの */
export type ResolvedProduct = BuyingGuideProduct & {
  /** ページ内で商品を指す安定したキー */
  key: string;
  item: OfferItem;
  pixel?: string;
  /** 提携商品（PR表記・rel="sponsored"） */
  sponsored: boolean;
};

const DIR = path.join(/*turbopackIgnore: true*/ process.cwd(), "content", "buying-guides");

let cache: BuyingGuide[] | null = null;

/**
 * 比較ガイドを公開（検索・メニュー・記事からの導線）するか。
 * 中身とデザインを作り直すまでは false：ページは残すが検索には出さず、導線も出さない。
 */
export const COMPARE_PUBLIC = false;

/** 「公式ページに記載なし」は表に出さない（比較表のセルは空欄、注意点の箇条書きは項目ごと消す。lib/no-data.ts） */
function withoutNoDataGuide(guide: BuyingGuide): BuyingGuide {
  return {
    ...guide,
    products: (guide.products ?? []).map((product) => ({
      ...product,
      specs: product.specs?.map(withoutNoData),
      limits: withoutNoDataList(product.limits),
      cells: product.cells && Object.fromEntries(Object.entries(product.cells).map(([k, v]) => [k, withoutNoData(v)])),
    })),
  };
}

export function getAllBuyingGuides(): BuyingGuide[] {
  // 開発中は生成スクリプトが書き足したガイドをすぐ見られるよう、毎回読み直す
  if (cache && process.env.NODE_ENV === "production") return cache;
  if (!fs.existsSync(DIR)) return (cache = []);
  cache = fs
    .readdirSync(DIR)
    .filter((file) => file.endsWith(".json"))
    .flatMap((file) => {
      try {
        return [withoutNoDataGuide(JSON.parse(fs.readFileSync(path.join(DIR, file), "utf8")) as BuyingGuide)];
      } catch {
        return [];
      }
    })
    .filter((guide) => resolveProducts(guide).length >= 2)
    .sort((a, b) => a.title.localeCompare(b.title, "ja"));
  return cache;
}

export function getBuyingGuide(slug: string): BuyingGuide | undefined {
  return getAllBuyingGuides().find((guide) => guide.slug === slug);
}

/**
 * 表示する商品（リンク・画像つき）。
 * 提携商品は提携が切れたら自動で消える。提携していない商品（name と officialUrl がある）は公式サイトへ案内する。
 */
export function resolveProducts(guide: BuyingGuide): ResolvedProduct[] {
  return guide.products.flatMap((product): ResolvedProduct[] => {
    const priceText = product.price || "公式サイトで確認";
    if (product.partner) {
      const found = partnerItem(product.partner);
      if (!found) return [];
      // 静的な比較ページは期限を迎えた時点で再生成される保証がないため、期限つきの販売は載せない。
      if (found.item.activeUntil) return [];
      const item = { ...found.item, price: null, priceText };
      return [{ ...product, key: product.id ?? product.partner, item, pixel: found.pixel, sponsored: true }];
    }
    if (!product.id || !product.name || !product.officialUrl) return [];
    const item: OfferItem = {
      key: product.id,
      kind: "partner",
      name: product.name,
      badge: product.category,
      image: null,
      price: null,
      priceText,
      url: product.officialUrl,
      shop: product.maker ?? product.name,
      cta: "公式サイトで見る",
    };
    return [{ ...product, key: product.id, item, sponsored: false }];
  });
}

/** 新形式（提携外の商品も並べ、判定・診断・詳しい比較表を持つ）か */
export function isRichGuide(guide: BuyingGuide): boolean {
  return (guide.schemaVersion ?? 1) >= 2;
}

export function guideRelatedArticles(guide: BuyingGuide, limit = 6): ArticleSummary[] {
  return guide.relatedArticleSlugs.flatMap((slug) => getArticleSummary(slug) ?? []).slice(0, limit);
}

/** 記事の商品枠の話題（offers.json のキー）に対応する比較ガイド */
export function buyingGuideForTopic(topic: string | undefined): BuyingGuide | undefined {
  if (!COMPARE_PUBLIC) return undefined;
  if (!topic) return undefined;
  return getAllBuyingGuides().find((guide) => guide.topics.includes(topic));
}

/** ガイドの見出しから「〇〇を選ぶなら」に使う短い名前（【】や：以降を落とす） */
export function guideShortName(guide: BuyingGuide): string {
  if (guide.shortName) return guide.shortName;
  return guide.title.split(/[【：:]/)[0].replace(/(おすすめ)?比較$/, "").replace(/おすすめ$/, "").trim();
}
