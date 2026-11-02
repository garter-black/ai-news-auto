import fs from "@/lib/content-fs";
import path from "node:path";
import type { ArticleSummary } from "@/lib/articles";

/**
 * 記事の広告枠（右側の1〜3位・本文中の4〜6位）。
 * 話題の定義は content/offers.json、本の候補は content/products.json（scripts/rakuten_books.py が更新）。
 * 記事ごとに全話題を採点し（scoreTopics）、基準点を超えた話題の商品だけを関連の高い順に並べる。
 * どの話題も届かない記事には広告を出さない（ページ側で lib/read-next.ts のガイドを案内する）。
 */

type Topic = {
  key: string;
  match: string;
  eyebrow: string;
  headline: string;
  lead: string;
  partners: string[];
  search?: string | string[];
  /** 商品が足りないときに補う話題 */
  fill?: string[];
  /** 本の題名に必要なテーマ */
  require?: string;
  /** この話題を使う記事のジャンル（旧形式。genres が無いときだけ使う。どちらも無ければ ai） */
  genre?: string;
  genres?: string[];
  /** 基準点（省略時 MIN_SCORE）。広い話題は高くして、見出し以外の裏付けも求める */
  min?: number;
  /** 見出しに当たったら、この話題を使わない */
  articleExclude?: string;
  /** 本だけを出し、提携商品は出さない */
  booksOnly?: boolean;
  /** 発表元（channelSlug・sourceName）に当たれば1点 */
  companies?: string;
  /** match に足す語。match は scripts/a8_import.py が上書きする話題があるので、追加の語はこちらに書く */
  alsoMatch?: string;
};

type Partner = {
  program: string;
  name: string;
  label: string;
  note?: string;
  price: number | null;
  priceText?: string;
  /** 手動確認した静的な料金の日時 */
  priceCheckedAt?: string;
  source?: string;
  image: string | null;
  page: string | null;
  shop: string;
  /** 写真の無いサービスで、名前の上に出す小さな見出し（例: AI TOOL / ONLINE SCHOOL） */
  badge?: string;
  /** A8 のバナー素材など、素材ごとのリンクを使う場合（画像と組で使う） */
  link?: string;
  pixel?: string;
  /** 在庫等の問題が確認された広告は、確認するまで表示しない */
  hidden?: boolean;
  /** 販売終了時刻（確認済みの案件だけ） */
  activeUntil?: string;
  facts?: string[];
  caution?: string;
  imageNote?: string;
  cta?: string;
};

type Book = {
  rbId: string;
  title: string;
  author: string;
  price: number;
  rating: number | null;
  reviews: number;
  image: string;
  url: string;
  /** 楽天ブックスの売れ筋順（検索語ごと）での順位 */
  rank?: number;
  keyword?: string;
};

type FallbackSet = { eyebrow: string; headline: string; lead: string; partners: string[]; bookTopics?: string[] };

type OffersFile = {
  /** 合う話題が無い記事に出す、ジャンルごとの広告（読者全体に役立つもの） */
  articleFallback?: { byGenre: Record<string, string>; default: string; sets: Record<string, FallbackSet> };
  topics: Topic[];
  audience: Record<string, string>;
  fallback: string;
  /** ジャンルごとの既定の話題 */
  fallbackByGenre?: Record<string, string>;
  partners: Record<string, Partner>;
};

type ProductsFile = {
  fetchedAt: string;
  pixel?: string;
  books: Record<string, Book[]>;
  partnerPrices?: Record<string, number>;
};

type AffiliateEntry = { url?: string; pixel?: string };

export type OfferItem = {
  key: string;
  kind: "partner" | "book";
  name: string;
  label?: string;
  note?: string;
  author?: string;
  price?: number | null;
  priceText?: string;
  /** この時刻を過ぎたら金額を表示しない。 */
  priceFreshUntil?: string;
  image?: string | null;
  url: string;
  shop: string;
  cta: string;
  rating?: number | null;
  reviews?: number;
  /** 楽天ブックス売れ筋の順位（10位以内のときだけ表示に使う） */
  rank?: number;
  keyword?: string;
  badge?: string;
  activeUntil?: string;
  facts?: string[];
  caution?: string;
  imageNote?: string;
  /** 記事との関連の点数（記事の枠だけ） */
  relevance?: number;
};

export type Offer = {
  topic: string;
  eyebrow: string;
  headline: string;
  lead: string;
  items: OfferItem[];
  /** 価格・評価の取得日（ISO） */
  checkedAt: string;
  /** ASPの表示計測用ピクセル */
  pixels: string[];
  timedPixels?: { url: string; activeUntil: string }[];
  /** いちばん関連の高い話題の点数（記事の枠だけ） */
  relevance?: number;
  /** 記事に合う話題が無く、ジャンルの既定の広告を出している */
  fallback?: boolean;
};

const MAX_ITEMS = 3;
const PRICE_MAX_AGE_MS = 48 * 60 * 60 * 1000;

function priceFreshUntil(checkedAt: string | undefined): string | undefined {
  const checked = Date.parse(checkedAt ?? "");
  return Number.isFinite(checked) ? new Date(checked + PRICE_MAX_AGE_MS).toISOString() : undefined;
}

function readJson<T>(file: string): T | null {
  // 引数のパスだとプロジェクト全体が同梱されるので追跡を止める（必要なファイルは next.config.ts で同梱）
  const full = path.join(/*turbopackIgnore: true*/ process.cwd(), file);
  if (!fs.existsSync(full)) return null;
  try {
    return JSON.parse(fs.readFileSync(full, "utf8")) as T;
  } catch {
    return null;
  }
}

let loaded: { offers: OffersFile; products: ProductsFile | null; affiliates: Record<string, AffiliateEntry> } | null = null;

function load() {
  if (loaded) return loaded;
  const offers = readJson<OffersFile>("content/offers.json") ?? {
    topics: [],
    audience: {},
    fallback: "",
    partners: {},
  };
  const products = readJson<ProductsFile>("content/products.json");
  const affiliates = readJson<Record<string, AffiliateEntry>>("affiliate_links.json") ?? {};
  loaded = { offers, products, affiliates };
  return loaded;
}

/** A8 の提携リンクに飛び先（商品ページ）を付ける。提携先が許可している商品リンクの形式 */
function partnerUrl(base: string, page: string | null): string {
  return page ? `${base}&a8ejpredirect=${encodeURIComponent(page)}` : base;
}

/** 話題の基準点。見出しに1語当たる（4点）か、タグと要約の両方で裏付けが取れる程度 */
export const MIN_SCORE = 4;
const WEIGHT = { title: 4, meta: 2, summary: 1 } as const;

function regex(source: string | undefined, flags = "i"): RegExp | undefined {
  if (!source) return undefined;
  try {
    return new RegExp(source, flags);
  } catch {
    return undefined;
  }
}

/**
 * 見出しの「ChatGPTのOpenAI」「AI半導体のNVIDIA」のような会社の説明を外す。
 * 説明の語で話題（ChatGPTの本など）が当たり、関係の薄い商品が出るのを防ぐ。
 */
function stripDescriptors(text: string): string {
  return text.replace(
    /(?:対話AI|生成AI|AI)?[「『]?(?:ChatGPT|Claude|Gemini|Copilot|AI半導体|半導体大手|半導体)[」』]?(?:の開発元|を開発する|を手がける|の)\s*(?=OpenAI|Anthropic|Google|グーグル|NVIDIA|エヌビディア|Microsoft|マイクロソフト)/gi,
    "",
  );
}

type ArticleText = { title: string; meta: string; summary: string; company: string };

function articleText(article: ArticleSummary): ArticleText {
  return {
    title: stripDescriptors(`${article.title} ${article.seoTitle ?? ""}`),
    meta: [...(article.tags ?? []), ...(article.keywords ?? [])].join(" / "),
    summary: [article.lede ?? "", article.description ?? "", ...(article.highlights ?? [])].join(" "),
    company: [article.channelSlug ?? "", article.channel ?? "", article.sourceName ?? ""].join(" "),
  };
}

function distinctHits(pattern: RegExp, text: string): number {
  const global = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`);
  const found = text.match(global);
  return found ? new Set(found.map((word) => word.toLowerCase())).size : 0;
}

/**
 * 正規表現1つが記事にどれだけ関係するか。見出し4点・タグ等2点・要約1点。
 * 同じ欄で別の語が当たるほど少し上げる（1欄につき最大 +1）。
 */
export function relevanceScore(pattern: string, article: ArticleSummary, text: ArticleText = articleText(article)): number {
  const re = regex(pattern);
  if (!re) return 0;
  let score = 0;
  for (const field of ["title", "meta", "summary"] as const) {
    const hits = distinctHits(re, text[field]);
    if (hits > 0) score += WEIGHT[field] + 0.5 * Math.min(hits - 1, 2);
  }
  return score;
}

function genresOf(topic: Topic): string[] {
  return topic.genres ?? [topic.genre ?? "ai"];
}

export type TopicScore = { topic: Topic; score: number };

/** 記事に合う話題を、点数の高い順に返す（基準点に届かない話題は返さない） */
export function scoreTopics(article: ArticleSummary): TopicScore[] {
  const { offers } = load();
  const text = articleText(article);
  const scored: TopicScore[] = [];
  for (const topic of offers.topics) {
    // ジャンルの違う話題（スポーツ記事に回線、AI記事に投資の本など）は出さない
    if (!genresOf(topic).includes(article.genreSlug)) continue;
    if (regex(topic.articleExclude)?.test(text.title)) continue;
    let score = relevanceScore(topic.alsoMatch ? `${topic.match}|${topic.alsoMatch}` : topic.match, article, text);
    // 発表元だけでは当てない（話題の語が記事に出てくるときの裏付けとして使う）
    if (score > 0 && regex(topic.companies)?.test(text.company)) score += 1;
    if (score >= (topic.min ?? MIN_SCORE)) scored.push({ topic, score });
  }
  // 同点は offers.json の並び（具体的な話題が上）のまま
  return scored.sort((a, b) => b.score - a.score);
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function offerForArticle(article: ArticleSummary, maxItems = MAX_ITEMS): Offer | undefined {
  // GeForce NOWはクラウドでゲームを動かすサービス。名前にGeForceが含まれるだけで
  // 高額なローカルAI向けPCを「この記事の選択肢」として勧めない。
  const headline = `${article.title} ${article.seoTitle ?? ""}`;
  if (/GeForce\s*NOW|クラウドゲーム/i.test(headline) && !/ローカルAI|ローカルLLM|Ollama|画像生成/i.test(headline)) return undefined;
  const scored = scoreTopics(article);
  if (scored.length === 0) return undefined;
  return buildArticleOffer(article, scored, maxItems);
}

export type OfferPlan = {
  /** 関連の高い順の候補すべて（最大12） */
  offer?: Offer;
  /** 右側（1〜3位）。RankingBody が見ていない商品を先に出せるよう予備を3つ持つ */
  rail?: Offer;
  /** 本文中（4〜6位）。右側とは重ならない。2つ未満なら出さない */
  inline?: Offer;
};

/**
 * 記事に合う話題が無いときの広告。どの記事にも広告枠を1つは出す（オーナー指示 2026-11-02）。
 * 読者（20〜50代の働く男性：仕事・お金・副業・AI）に幅広く役立つ商品を、ジャンルごとに content/offers.json の articleFallback で決める。
 */
export function fallbackOfferForArticle(article: ArticleSummary, maxItems = 12): Offer | undefined {
  const { offers, products } = load();
  const config = offers.articleFallback;
  if (!config) return undefined;
  const key = config.byGenre[article.genreSlug] ?? config.default;
  const set = config.sets[key] ?? config.sets[config.default];
  if (!set) return undefined;
  const built: Built[] = [];
  for (const partner of set.partners) {
    const item = partnerOffer(partner);
    if (item) built.push({ item: { ...item.item, relevance: 0 }, pixel: item.pixel });
  }
  const seen = new Set<string>();
  for (const topic of set.bookTopics ?? []) {
    for (const book of rotate(products?.books?.[topic] ?? [], article.slug || topic)) {
      if (built.length >= maxItems) break;
      if (seen.has(book.rbId)) continue;
      seen.add(book.rbId);
      built.push({ item: { ...bookOffer(book), relevance: 0 } });
    }
  }
  const topic: Topic = { key: `fallback-${key}`, match: "", eyebrow: set.eyebrow, headline: set.headline, lead: set.lead, partners: set.partners };
  return finishOffer(topic, built.slice(0, maxItems), { relevance: 0, fallback: true });
}

/** 記事ページの広告枠の割り当て。右側と本文中で同じ商品を出さない。合う話題が無ければジャンルの既定の広告 */
export function offerPlanForArticle(article: ArticleSummary): OfferPlan {
  const offer = offerForArticle(article, 12) ?? fallbackOfferForArticle(article, 12);
  if (!offer) return {};
  const pool = offer.items;
  const rail = { ...offer, items: [...pool.slice(0, 3), ...pool.slice(6, 9)] };
  const inlineItems = [...pool.slice(3, 6), ...pool.slice(9, 12)];
  const inline = inlineItems.length >= 2 ? { ...offer, eyebrow: "あわせてチェック", items: inlineItems } : undefined;
  return { offer, rail, inline };
}

/** 記事に紐づかないページ（一覧ページなど）で、話題を指定して商品枠を作る */
export function offerForTopic(key: string, eyebrow?: string, maxItems = MAX_ITEMS): Offer | undefined {
  const { offers } = load();
  const found = offers.topics.find((t) => t.key === key);
  if (!found) return undefined;
  // 記事ページ以外では「この記事の〜」という見出しが合わないので差し替える
  const topic = eyebrow ? { ...found, eyebrow } : found;
  return buildOffer(topic, offers.fallbackByGenre?.[genresOf(topic)[0]] ?? offers.fallback, maxItems);
}

/**
 * 提携商品1つを、商品枠と同じ形（A8 のリンク・画像つき）で取り出す。比較ガイド用。
 * 提携リンクが無い（未提携・提携切れ）ものは undefined
 */
export function partnerItem(key: string): { item: OfferItem; pixel?: string } | undefined {
  const { offers, affiliates } = load();
  const partner = offers.partners[key];
  const program = partner ? affiliates[partner.program] : undefined;
  if (!partner || partner.hidden || !program?.url) return undefined;
  return {
    item: {
      key,
      kind: "partner",
      name: partner.name,
      label: partner.label,
      badge: partner.badge,
      note: partner.note,
      activeUntil: partner.activeUntil,
      facts: partner.facts,
      caution: partner.caution,
      imageNote: partner.imageNote,
      image: partner.image,
      url: partner.link ?? partnerUrl(program.url, partner.page),
      shop: partner.shop,
      cta: partner.cta ?? "公式サイトで見る",
    },
    pixel: partner.pixel ?? program.pixel,
  };
}

/** 文字列から決まった数を作る（同じ記事なら同じ並び、記事が違えば違う並びにするため） */
function hashOf(text: string): number {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return h;
}

/**
 * 話題の本を、日付と記事ごとにずらして並べる。
 * 同じ話題の記事ばかり読む人にも、日や記事が変われば違う本が見えるようにする。
 * サイトは数時間ごとに作り直すので、日付を混ぜれば毎日入れ替わる。
 */
function rotate<T>(list: T[], seed: string): T[] {
  if (list.length <= 1) return list;
  const day = Math.floor(Date.now() / 86_400_000);
  const start = (hashOf(seed) + day) % list.length;
  return [...list.slice(start), ...list.slice(0, start)];
}

type Built = { item: OfferItem; pixel?: string };

/** 提携商品1つを枠の形にする。非表示・提携リンクの無いものは undefined。リンク・ピクセルは登録値のまま */
function partnerOffer(key: string): Built | undefined {
  const { offers, products, affiliates } = load();
  const partner = offers.partners[key];
  const program = partner ? affiliates[partner.program] : undefined;
  if (!partner || partner.hidden || !program?.url) return undefined;
  return {
    item: {
      key,
      kind: "partner",
      name: partner.name,
      label: partner.label,
      badge: partner.badge,
      note: partner.note,
      activeUntil: partner.activeUntil,
      facts: partner.facts,
      caution: partner.caution,
      imageNote: partner.imageNote,
      price: products?.partnerPrices?.[key] ?? partner.price,
      priceText: partner.priceText,
      priceFreshUntil: priceFreshUntil(partner.source ? products?.fetchedAt : partner.priceCheckedAt),
      image: partner.image,
      url: partner.link ?? partnerUrl(program.url, partner.page),
      shop: partner.shop,
      cta: partner.cta ?? "公式サイトで見る",
    },
    pixel: partner.pixel ?? program.pixel,
  };
}

function bookOffer(book: Book): OfferItem {
  const { products } = load();
  return {
    key: book.rbId,
    kind: "book",
    name: book.title,
    author: book.author,
    price: book.price,
    priceFreshUntil: priceFreshUntil(products?.fetchedAt),
    image: book.image,
    url: book.url,
    shop: "楽天ブックス",
    cta: "楽天で見る",
    rating: book.rating,
    reviews: book.reviews,
    rank: book.rank,
    keyword: book.keyword,
  };
}

function finishOffer(topic: Topic, built: Built[], extra: Partial<Offer> = {}): Offer | undefined {
  const { products } = load();
  if (built.length === 0) return undefined;
  const pixels = new Set<string>();
  const timedPixels = new Map<string, string>();
  for (const { item, pixel } of built) {
    if (pixel && item.activeUntil) timedPixels.set(pixel, item.activeUntil);
    else if (pixel) pixels.add(pixel);
  }
  if (built.some(({ item }) => item.kind === "book") && products?.pixel) pixels.add(products.pixel);
  return {
    topic: topic.key,
    eyebrow: topic.eyebrow,
    headline: topic.headline,
    lead: topic.lead,
    items: built.map(({ item }) => item),
    checkedAt: products?.fetchedAt ?? new Date().toISOString(),
    pixels: [...pixels],
    timedPixels: [...timedPixels].filter(([url]) => !pixels.has(url)).map(([url, activeUntil]) => ({ url, activeUntil })),
    ...extra,
  };
}

/**
 * 記事の枠。基準点を超えた話題すべての商品を集め、関連の高い順に並べる。
 * - 商品の点数 = その商品を含む話題の最高点（話題内の並びで少しずつ下げる）
 * - 見出しに商品名が出ていれば +3（その商品を扱う記事）
 * - 本は題名が話題の require にも当たるものだけ、同じ話題の提携商品より1点下
 * 別の話題の本や既定の話題で枠を埋めることはしない。
 */
function buildArticleOffer(article: ArticleSummary, scored: TopicScore[], maxItems: number): Offer | undefined {
  const { offers, products } = load();
  const text = articleText(article);
  const best = new Map<string, Built & { relevance: number }>();
  const consider = (built: Built | undefined, relevance: number) => {
    if (!built) return;
    const current = best.get(built.item.key);
    if (!current || current.relevance < relevance) best.set(built.item.key, { ...built, relevance });
  };
  for (const { topic, score } of scored) {
    if (!topic.booksOnly) {
      topic.partners.forEach((key, index) => {
        const partner = offers.partners[key];
        const named = partner && [partner.program, partner.name].some((name) => name && regex(escapeRegex(name))?.test(text.title));
        consider(partnerOffer(key), score - index * 0.1 + (named ? 3 : 0));
      });
    }
    const require = regex(topic.require);
    rotate(products?.books?.[topic.key] ?? [], article.slug || topic.key).forEach((book, index) => {
      if (require && !require.test(book.title)) return;
      consider({ item: bookOffer(book) }, score - 1 - index * 0.01);
    });
  }
  const ranked = [...best.values()]
    .sort((a, b) => b.relevance - a.relevance)
    .slice(0, maxItems)
    .map(({ item, pixel, relevance }) => ({ item: { ...item, relevance: Math.round(relevance * 10) / 10 }, pixel }));
  return finishOffer(scored[0].topic, ranked, { relevance: scored[0].score });
}

/** 一覧ページ用（offerForTopic）。指定の話題の商品と本を並べ、足りなければ fill の話題で補う */
function buildOffer(topic: Topic, fallback: string, maxItems: number): Offer | undefined {
  const { products } = load();
  const built: Built[] = [];
  for (const key of topic.partners) {
    const item = partnerOffer(key);
    if (item) built.push(item);
  }
  const seen = new Set<string>();
  for (const source of [topic.key, ...(topic.fill ?? []), fallback]) {
    const books = products?.books?.[source] ?? [];
    for (const book of source === topic.key ? rotate(books, topic.key) : books) {
      if (built.length >= maxItems) break;
      if (seen.has(book.rbId)) continue;
      seen.add(book.rbId);
      built.push({ item: bookOffer(book) });
    }
  }
  return finishOffer(topic, built);
}
