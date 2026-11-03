import fs from "@/lib/content-fs";
import path from "node:path";
import type { ArticleSummary } from "@/lib/articles";
import { amazonUrl, rakutenSearchUrl } from "@/lib/amazon";
import { productCategoryForArticle } from "@/lib/keyword-products";

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
  /** 話題の短い名前（例: Gemini・セキュリティ対策）。枠の商品が複数の話題にまたがるとき、見出しをこの名前から作る */
  name?: string;
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

/** 法人・企業向けの記事の扱い（content/offers.json の b2b）。isB2BArticle を参照 */
type B2BConfig = {
  match: string;
  genres?: string[];
  hostingPartners?: string[];
  hostingTopics?: string[];
  consumerTopics?: string[];
  penalty?: number;
  preferTopics?: string[];
  bonus?: number;
  fallbackSet?: string;
};

type OffersFile = {
  /** 合う話題が無い記事に出す、ジャンルごとの広告（読者全体に役立つもの） */
  articleFallback?: {
    byGenre: Record<string, string>;
    /** 見出しがお金の話題（moneyMatch）でないときに使う組（例: 公共の褒章の記事に会計ソフトを出さない） */
    byGenreIfNotMoney?: Record<string, string>;
    /** 見出しがお金の話題のときに使う組（例: ビジネスの日銀・物価の記事は証券・お金の本） */
    byGenreIfMoney?: Record<string, string>;
    moneyMatch?: string;
    default: string;
    sets: Record<string, FallbackSet>;
  };
  topics: Topic[];
  audience: Record<string, string>;
  fallback: string;
  /** ジャンルごとの既定の話題 */
  fallbackByGenre?: Record<string, string>;
  partners: Record<string, Partner>;
  b2b?: B2BConfig;
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
  /** この商品を選んだ話題（offers.json の topics の key。既定の広告は fallback-<セット名>）。枠の見出しを商品に合わせるのに使う */
  topic?: string;
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

/** 未設定のまま残った値（YOUR_ID など）。リンク・ピクセルのどこかに含まれていたら出さない */
const PLACEHOLDER = /YOUR[_-]?ID|your_aff_link|PLACEHOLDER|XXXX|example\.(?:com|jp|net)|[<>{}]/i;
/** A8 の素材ID（a8mat=4BEPPU+DBVECY+3XCC+6AZAQ の形） */
const A8MAT = /^[0-9A-Z]+(?:\+[0-9A-Z]+){3}$/;

/**
 * 実在する提携リンクの形か。計測IDの無い公式サイトや、プレースホルダの残ったリンクは広告として出さない。
 * A8（px/rpx.a8.net）は a8mat の形まで確かめる。それ以外は主要な ASP・楽天・Amazon の配信元だけ認める。
 */
export function isValidAffiliateUrl(raw: string | null | undefined): boolean {
  if (!raw || typeof raw !== "string") return false;
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    // 壊れたエンコードはそのまま照合する
  }
  if (PLACEHOLDER.test(raw) || PLACEHOLDER.test(decoded)) return false;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return false;
  }
  if (url.protocol !== "https:") return false;
  const host = url.hostname.toLowerCase();
  if (host === "px.a8.net" || host === "rpx.a8.net") {
    // searchParams は「+」を空白に変えるので、生の値で確かめる
    const a8mat = /[?&]a8mat=([^&#]*)/.exec(url.search)?.[1] ?? "";
    return url.pathname === "/svt/ejp" && A8MAT.test(a8mat);
  }
  if (host === "af.moshimo.com") return /^\d+$/.test(url.searchParams.get("a_id") ?? "");
  if (host === "ck.jp.ap.valuecommerce.com") return /^\d+$/.test(url.searchParams.get("sid") ?? "") && /^\d+$/.test(url.searchParams.get("pid") ?? "");
  if (host === "h.accesstrade.net") return url.pathname.length > 1 || url.search.length > 1;
  if (host === "t.afi-b.com") return url.pathname.length > 1;
  if (host === "hb.afl.rakuten.co.jp") return url.pathname.length > 1;
  if (host === "amzn.to") return url.pathname.length > 1;
  if (host === "www.amazon.co.jp" || host === "amazon.co.jp") return /^[\w-]+-22$/.test(url.searchParams.get("tag") ?? "");
  return false;
}

/** 表示計測のピクセル。ASP の配信元で、プレースホルダが無いものだけ使う */
function validPixel(raw: string | null | undefined): string | undefined {
  if (!raw || PLACEHOLDER.test(raw)) return undefined;
  try {
    const url = new URL(raw);
    return url.protocol === "https:" && /(?:^|\.)(?:a8\.net|moshimo\.com|valuecommerce\.com|accesstrade\.net|afi-b\.com|rakuten\.co\.jp)$/i.test(url.hostname) ? raw : undefined;
  } catch {
    return undefined;
  }
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
  const b2b = isB2BArticle(article, text) ? offers.b2b : undefined;
  const scored: TopicScore[] = [];
  for (const topic of offers.topics) {
    // ジャンルの違う話題（スポーツ記事に回線、AI記事に投資の本など）は出さない
    if (!genresOf(topic).includes(article.genreSlug)) continue;
    if (regex(topic.articleExclude)?.test(text.title)) continue;
    let score = relevanceScore(topic.alsoMatch ? `${topic.match}|${topic.alsoMatch}` : topic.match, article, text);
    // 発表元だけでは当てない（話題の語が記事に出てくるときの裏付けとして使う）
    if (score > 0 && regex(topic.companies)?.test(text.company)) score += 1;
    // 法人・企業向けの記事: 個人向けの話題（副業・家の回線など）は下げて、見出しにはっきり出ているときだけ選ぶ。
    // 仕事・AIツール・スキルの話題は、基準点を超えたものだけ順位を上げる（届かない話題を引き上げはしない）
    if (score > 0 && b2b?.consumerTopics?.includes(topic.key)) score -= b2b.penalty ?? 2;
    if (score < (topic.min ?? MIN_SCORE)) continue;
    scored.push({ topic, score: score + (b2b?.preferTopics?.includes(topic.key) ? (b2b.bonus ?? 1) : 0) });
  }
  // 同点は offers.json の並び（具体的な話題が上）のまま
  return scored.sort((a, b) => b.score - a.score);
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 法人・企業向けの記事か（ネットワーク・基幹システム・エンタープライズ向けAIなど）。
 * 見出しに1語当たるか、タグと要約の両方で裏付けが取れる程度（広告の話題と同じ基準点）。
 * こうした記事の読者に個人向けのレンタルサーバーを勧めない（記事の話題がサーバーそのもののときを除く）。
 */
export function isB2BArticle(article: ArticleSummary, text: ArticleText = articleText(article)): boolean {
  const config = load().offers.b2b;
  if (!config?.match) return false;
  if (config.genres && !config.genres.includes(article.genreSlug)) return false;
  return relevanceScore(config.match, article, text) >= MIN_SCORE;
}

/** 記事の話題がサーバー・セルフホストそのものか（offers.json の b2b.hostingTopics の話題が基準点を超えるか） */
export function matchesHostingTopic(article: ArticleSummary, text: ArticleText = articleText(article)): boolean {
  const { offers } = load();
  const keys = offers.b2b?.hostingTopics ?? [];
  return offers.topics.some((topic) => {
    if (!keys.includes(topic.key) || !genresOf(topic).includes(article.genreSlug)) return false;
    const pattern = topic.alsoMatch ? `${topic.match}|${topic.alsoMatch}` : topic.match;
    return relevanceScore(pattern, article, text) >= (topic.min ?? MIN_SCORE);
  });
}

/** 法人・企業向けの記事で、個人向けのサーバー・VPS を外すか */
function blocksHosting(article: ArticleSummary, text: ArticleText): boolean {
  return isB2BArticle(article, text) && !matchesHostingTopic(article, text);
}

/**
 * 枠の見出し（eyebrow・headline・lead）を、枠に実際に並ぶ商品の話題から作る。
 * 全部が同じ話題ならその話題の見出し。複数の話題にまたがるときは、話題の短い名前を並べた見出しにする
 * （「Geminiを使いこなす」の下に VPN が並ぶような、見出しと商品のずれを出さない）。
 */
function headingFor(items: OfferItem[], fallbackTopic: Topic): Pick<Offer, "topic" | "eyebrow" | "headline" | "lead"> {
  const { offers } = load();
  const order: string[] = [];
  for (const item of items) if (item.topic && !order.includes(item.topic)) order.push(item.topic);
  const find = (key: string) => (key === fallbackTopic.key ? fallbackTopic : offers.topics.find((t) => t.key === key));
  const topics = order.map(find).filter((t): t is Topic => Boolean(t));
  if (topics.length <= 1) {
    const topic = topics[0] ?? fallbackTopic;
    return { topic: topic.key, eyebrow: topic.eyebrow, headline: topic.headline, lead: topic.lead };
  }
  const names = [...new Set(topics.map((t) => t.name || t.key))];
  const shown = names.length > 2 ? `${names.slice(0, 2).join("・")}など` : names.join("と");
  const hasBook = items.some((item) => item.kind === "book");
  const hasPartner = items.some((item) => item.kind === "partner");
  const kinds = hasBook && hasPartner ? "サービスと本" : hasBook ? "本" : "サービス";
  return {
    topic: topics[0].key,
    eyebrow: "この記事の話題に合わせて",
    headline: `${shown}に役立つ${kinds}`,
    lead: `${names.join("・")}に関わる${kinds}を、記事との関連が高い順に並べています。料金や条件はリンク先で確認してください。`,
  };
}

/**
 * 関連の高い順の商品から、1つの枠に入れる商品を選ぶ。いちばん上の商品の話題の商品を先に集め（最大 max）、
 * それで visible に届かないときだけ、残りから関連の高い順に足す（見出しは headingFor が実際の商品から作る）。
 */
function pickBox(pool: OfferItem[], max: number, visible: number): OfferItem[] {
  if (pool.length === 0) return [];
  const lead = pool[0].topic;
  const box = pool.filter((item) => item.topic === lead).slice(0, max);
  for (const item of pool) {
    if (box.length >= Math.min(visible, max)) break;
    if (!box.includes(item)) box.push(item);
  }
  // 関連の高い順のまま
  return pool.filter((item) => box.includes(item));
}

/**
 * 選んだ商品だけの枠にする（見出しは商品の話題から）。
 * 表示計測のピクセルは従来どおり候補全体の分をそのまま使う（ASP の計測の扱いを変えない）
 */
function boxOffer(offer: Offer, items: OfferItem[], baseTopic: Topic): Offer {
  return { ...offer, ...headingFor(items, baseTopic), items };
}

export function offerForArticle(article: ArticleSummary, maxItems = MAX_ITEMS): Offer | undefined {
  // GeForce NOWはクラウドでゲームを動かすサービス。名前にGeForceが含まれるだけで
  // 高額なローカルAI向けPCを「この記事の選択肢」として勧めない（articleScored）。
  const scored = articleScored(article);
  if (scored.length === 0) return undefined;
  const offer = buildArticleOffer(article, scored, maxItems);
  if (!offer) return undefined;
  // 枠の見出しは並ぶ商品の話題から（いちばん関連の高い商品の話題を中心に選ぶ）
  return boxOffer(offer, pickBox(offer.items, maxItems, MAX_ITEMS), scored[0].topic);
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
  const text = articleText(article);
  // 法人・企業向けの記事は、個人向けの副業・家の回線ではなく AIツール・スキル・仕事の効率化（offers.json の b2b）
  const b2bSet = isB2BArticle(article, text) ? offers.b2b?.fallbackSet : undefined;
  const moneyTitle = regex(config.moneyMatch)?.test(text.title) ?? false;
  const genreKey = (moneyTitle ? config.byGenreIfMoney?.[article.genreSlug] : config.byGenreIfNotMoney?.[article.genreSlug]) ?? config.byGenre[article.genreSlug] ?? config.default;
  const key = b2bSet && config.sets[b2bSet] ? b2bSet : genreKey;
  const set = config.sets[key] ?? config.sets[config.default];
  if (!set) return undefined;
  const hosting = blocksHosting(article, text) ? new Set(offers.b2b?.hostingPartners ?? []) : new Set<string>();
  const topicKey = `fallback-${key}`;
  const built: Built[] = [];
  for (const partner of set.partners) {
    if (hosting.has(partner)) continue;
    const item = partnerOffer(partner);
    if (item) built.push({ item: { ...item.item, relevance: 0, topic: topicKey }, pixel: item.pixel });
  }
  const seen = new Set<string>();
  for (const topic of set.bookTopics ?? []) {
    for (const book of rotate(validBooks(products?.books?.[topic]), article.slug || topic)) {
      if (built.length >= maxItems) break;
      if (seen.has(book.rbId)) continue;
      seen.add(book.rbId);
      built.push({ item: { ...bookOffer(book), relevance: 0, topic: topicKey } });
    }
  }
  const topic: Topic = { key: topicKey, match: "", eyebrow: set.eyebrow, headline: set.headline, lead: set.lead, partners: set.partners };
  return finishOffer(topic, built.slice(0, maxItems), { relevance: 0, fallback: true });
}

/**
 * 製品・趣味の記事（自転車・端末・ゲーム・スポーツ用品など）で、記事に合う話題が無いときの「この記事に関連する商品」。
 * 製品カテゴリの語（lib/keyword-products.ts の表だけで決める）で、楽天市場（A8 の楽天アフィリエイト。lib/amazon.ts と同じ形）
 * と Amazon（アソシエイト）の検索結果へ案内する。価格・宣伝文句は出さない。
 */
export function keywordProductOffer(article: ArticleSummary): Offer | undefined {
  const category = productCategoryForArticle(article);
  if (!category) return undefined;
  const { products } = load();
  const topicKey = "fallback-product";
  const note = `記事で扱った${category.name}に関連する商品を、検索結果で比べられます。特定の商品をすすめるものではありません。`;
  const caution = "価格・在庫・仕様は各ショップのページで確認してください。";
  const shops = [
    { id: "rakuten", shop: "楽天市場", badge: "RAKUTEN", url: rakutenSearchUrl(category.query), pixel: validPixel(products?.pixel) },
    { id: "amazon", shop: "Amazon", badge: "AMAZON", url: amazonUrl({ query: category.query }), pixel: undefined },
  ];
  const built: Built[] = shops
    .filter((shop) => isValidAffiliateUrl(shop.url))
    .map((shop) => ({
      item: {
        key: `${shop.id}-search:${category.query}`,
        kind: "partner" as const,
        name: `「${category.query}」を${shop.shop}で探す`,
        label: `${shop.shop}の検索結果`,
        badge: shop.badge,
        note,
        caution,
        image: null,
        url: shop.url,
        shop: shop.shop,
        cta: `${shop.shop}で探す`,
        relevance: 0,
        topic: topicKey,
      },
      pixel: shop.pixel,
    }));
  const topic: Topic = {
    key: topicKey,
    match: "",
    eyebrow: "この記事に関連する商品",
    headline: `この記事に関連する商品：${category.name}`,
    lead: "記事で扱った製品のカテゴリで、通販サイトの検索結果を案内します。",
    partners: [],
  };
  return finishOffer(topic, built, { relevance: 0, fallback: true });
}

/**
 * 記事ページの広告枠の割り当て。右側と本文中で同じ商品を出さない。
 * 1. 記事に合う話題の広告（関連の高い順） 2. 製品・趣味の記事ならそのカテゴリの商品検索 3. ジャンルの既定の広告
 */
export function offerPlanForArticle(article: ArticleSummary): OfferPlan {
  const scored = articleScored(article);
  const full = (scored.length > 0 ? buildArticleOffer(article, scored, 12) : undefined) ?? keywordProductOffer(article) ?? fallbackOfferForArticle(article, 12);
  if (!full) return {};
  const base = scored[0]?.topic ?? topicOfFallback(full);
  const pool = full.items;
  // 右側（1〜3位＋予備3つ）は、いちばん関連の高い商品の話題を中心に。本文中（4〜6位＋予備）は残りから同じ選び方で（右側と重ならない）。
  // 各枠の見出しは、その枠に並ぶ商品の話題から作る（headingFor）
  // まず見える3つずつ（右側→本文中）を決め、残りを同じ話題の予備として各枠に3つまで足す
  const railShown = pickBox(pool, MAX_ITEMS, MAX_ITEMS);
  const inlineShown = pickBox(pool.filter((item) => !railShown.includes(item)), MAX_ITEMS, MAX_ITEMS);
  const spare = (shown: OfferItem[], taken: OfferItem[]) => {
    const topics = new Set(shown.map((item) => item.topic));
    return pool.filter((item) => !taken.includes(item) && topics.has(item.topic)).slice(0, MAX_ITEMS);
  };
  const railItems = [...railShown, ...spare(railShown, [...railShown, ...inlineShown])];
  const inlineItems = [...inlineShown, ...spare(inlineShown, [...railItems, ...inlineShown])];
  const offer = boxOffer(full, pool, base);
  const rail = boxOffer(full, railItems, base);
  const inline = inlineItems.length >= 2 ? boxOffer(full, inlineItems, base) : undefined;
  return { offer: { ...offer, ...headingFor(railItems, base) }, rail, inline };
}

/** offerForArticle と同じ条件で話題を採点する（GeForce NOW の記事などは対象外） */
function articleScored(article: ArticleSummary): TopicScore[] {
  const headline = `${article.title} ${article.seoTitle ?? ""}`;
  if (/GeForce\s*NOW|クラウドゲーム/i.test(headline) && !/ローカルAI|ローカルLLM|Ollama|画像生成/i.test(headline)) return [];
  return scoreTopics(article);
}

/** 既定の広告の枠から、見出しの元になる話題を作り直す */
function topicOfFallback(offer: Offer): Topic {
  return { key: offer.topic, match: "", eyebrow: offer.eyebrow, headline: offer.headline, lead: offer.lead, partners: [] };
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
  const url = partner.link ?? partnerUrl(program.url, partner.page);
  // 未設定（YOUR_ID など）や計測IDの無いリンクは出さない
  if (!isValidAffiliateUrl(url)) return undefined;
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
      url,
      shop: partner.shop,
      cta: partner.cta ?? "公式サイトで見る",
    },
    pixel: validPixel(partner.pixel ?? program.pixel),
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
  const url = partner.link ?? partnerUrl(program.url, partner.page);
  // 未設定（YOUR_ID など）や計測IDの無いリンクは出さない
  if (!isValidAffiliateUrl(url)) return undefined;
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
      url,
      shop: partner.shop,
      cta: partner.cta ?? "公式サイトで見る",
    },
    pixel: validPixel(partner.pixel ?? program.pixel),
  };
}

/** 提携リンクの形が正しい本だけ */
function validBooks(list: Book[] | undefined): Book[] {
  return (list ?? []).filter((book) => isValidAffiliateUrl(book.url));
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
  const bookPixel = validPixel(products?.pixel);
  if (built.some(({ item }) => item.kind === "book") && bookPixel) pixels.add(bookPixel);
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
  // 法人・企業向けの記事には、個人向けのレンタルサーバー・VPS を出さない（記事の話題がサーバーのときを除く）
  const hosting = blocksHosting(article, text) ? new Set(offers.b2b?.hostingPartners ?? []) : new Set<string>();
  const best = new Map<string, Built & { relevance: number; topic: string }>();
  const consider = (built: Built | undefined, relevance: number, topic: string) => {
    if (!built) return;
    const current = best.get(built.item.key);
    if (!current || current.relevance < relevance) best.set(built.item.key, { ...built, relevance, topic });
  };
  for (const { topic, score } of scored) {
    if (!topic.booksOnly) {
      topic.partners.forEach((key, index) => {
        const partner = offers.partners[key];
        const named = partner && [partner.program, partner.name].some((name) => name && regex(escapeRegex(name))?.test(text.title));
        if (hosting.has(key) && !named) return;
        consider(partnerOffer(key), score - index * 0.1 + (named ? 3 : 0), topic.key);
      });
    }
    const require = regex(topic.require);
    rotate(validBooks(products?.books?.[topic.key]), article.slug || topic.key).forEach((book, index) => {
      if (require && !require.test(book.title)) return;
      consider({ item: bookOffer(book) }, score - 1 - index * 0.01, topic.key);
    });
  }
  const ranked = [...best.values()]
    .sort((a, b) => b.relevance - a.relevance)
    .slice(0, maxItems)
    .map(({ item, pixel, relevance, topic }) => ({ item: { ...item, relevance: Math.round(relevance * 10) / 10, topic }, pixel }));
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
    const books = validBooks(products?.books?.[source]);
    for (const book of source === topic.key ? rotate(books, topic.key) : books) {
      if (built.length >= maxItems) break;
      if (seen.has(book.rbId)) continue;
      seen.add(book.rbId);
      built.push({ item: bookOffer(book) });
    }
  }
  return finishOffer(topic, built);
}
