import type { ArticleSummary } from "@/lib/articles";

/**
 * 記事ページの「関連記事」「次に読むならこれ」（同じ話題の記事・同じ企業の発表）の選び方（2026-11-03 オーナー判断・台帳 W19）。
 *
 * 前の選び方は「同じジャンル」に5点を足していたので、中身と関係のない同じジャンルの新着が並んでいた
 * （Appleの決算 → サムスンのユース五輪・IPAのDX認定、年金月間 → 【地震】、フットサル → OpenAIのバグ報奨金）。
 * 新しい選び方:
 *   1. 話題の重なり: タグ・キーワード・見出しの固有名詞（英字の名前・3字以上のカタカナ・「」の中）のうち、
 *      多くの記事に付く語（ジャンル名・「統計」「ビジネス」など）を除いた語が重なるか。珍しい語ほど重い（IDF）
 *   2. 同じ企業（companies.ts の分類。「その他」は数えない）
 *   3. 同じジャンルは小さな加点だけ（ジャンルだけでは選ばない）
 *   4. 新しい記事を少し上に
 * 基準に届く記事が無ければ何も出さない（関係のない記事で埋めない）。地震・津波の記事とそれ以外は混ぜない。
 * 同じ発表の重複記事（duplicateOf・同じ出典URL）は選ばない。
 */

/** どの記事にも付きやすく、話題の手がかりにならない語（ジャンル名・分野名・発表の種類） */
const GENERIC = new Set(
  [
    "AI", "AIツール", "AIニュース", "生成AI", "人工知能", "テクノロジー", "テック", "AIエージェント", "LLM", "使い方", "料金", "無料",
    "日本語", "とは", "開発", "アップデート", "ニュース", "発表", "調査", "ビジネス", "ビジネス・経済", "経済", "統計", "統計情報", "行政",
    "社会・行政", "社会", "暮らし", "暮らし・生活", "生活", "ライフスタイル", "スポーツ", "エンタメ", "テクノロジー・ガジェット", "ガジェット",
    "投資・マネー", "金融", "IT", "DX", "アプリ", "通信", "セキュリティ", "提携", "業務提携", "連携", "協定", "地域", "イベント",
    "キャンペーン", "セール", "新製品", "新サービス", "デザイン", "トレンド", "環境", "政策", "教育", "日本", "国内", "海外", "企業",
    "サービス", "公式", "発表会", "2026年", "2027年", "令和8年",
  ].map(norm),
);

export type CompanyOf = (a: ArticleSummary) => string;

export type NextRead = { article: ArticleSummary; reason: string };

type Ctx = { df: Map<string, number>; n: number; cap: number; terms: WeakMap<ArticleSummary, Set<string>>; names: WeakMap<ArticleSummary, Set<string>> };
const contexts = new WeakMap<object, Ctx>();

function norm(word: string): string {
  return (word ?? "").normalize("NFKC").trim().toLowerCase();
}

/** 英字の略語・カタカナの一般語（固有名詞ではない。見出しに出ても同じ話題の手がかりにしない） */
const NAME_STOP = new Set(
  [
    "AI", "IT", "DX", "PDF", "API", "SNS", "CEO", "CFO", "URL", "APP", "PC", "OS", "CPU", "GPU", "NPU", "IOT", "EV", "PHEV", "HEV", "SDK", "UI",
    "WEB", "CVE", "FAQ", "ETF", "LLM", "GX", "SDGS", "PR", "IR", "Q", "Pro", "Max", "Plus", "Ultra", "Mini", "Air", "New",
    "サービス", "システム", "データ", "ネット", "アプリ", "ユーザー", "プラン", "モデル", "ツール", "セキュリティ", "ガイドライン", "プログラム",
    "プロジェクト", "ビジネス", "マッチング", "イベント", "キャンペーン", "オンライン", "デジタル", "テクノロジー", "ソリューション", "プラットフォーム",
    "コンテンツ", "サポート", "アップデート", "リリース", "パートナー", "グループ", "ホールディングス", "センター", "クラウド", "ポイント", "トレンド",
  ].map(norm),
);

/** 見出しの固有名詞らしい語（英字の名前・3字以上のカタカナ・「」『』の中） */
function titleTerms(title: string): string[] {
  const t = (title ?? "").normalize("NFKC");
  const out: string[] = [];
  for (const m of t.matchAll(/[「『]([^」』]{2,24})[」』]/g)) out.push(m[1]);
  for (const m of t.matchAll(/[A-Za-z][A-Za-z0-9.+\-]*[A-Za-z0-9+]/g)) out.push(m[0]);
  for (const m of t.matchAll(/[ァ-ヴー]{3,}/g)) out.push(m[0]);
  return out.map(norm).filter((w) => w.length >= 2 && !NAME_STOP.has(w) && !GENERIC.has(w));
}

function rawTerms(a: ArticleSummary): Set<string> {
  const words = [...(a.tags ?? []), ...(a.keywords ?? []), ...titleTerms(a.title)];
  return new Set(words.map(norm).filter((w) => w.length >= 2 && !GENERIC.has(w) && !/^\d+$/.test(w)));
}

function contextOf(candidates: ArticleSummary[]): Ctx {
  let ctx = contexts.get(candidates);
  if (ctx) return ctx;
  const terms = new WeakMap<ArticleSummary, Set<string>>();
  const df = new Map<string, number>();
  for (const a of candidates) {
    const set = rawTerms(a);
    terms.set(a, set);
    for (const w of set) df.set(w, (df.get(w) ?? 0) + 1);
  }
  const n = Math.max(candidates.length, 1);
  // 全記事の4%（最低8本）を超えて付いている語は、話題ではなくジャンル・分野・発表元の名前とみなす
  ctx = { df, n, cap: Math.max(8, Math.round(n * 0.04)), terms, names: new WeakMap() };
  contexts.set(candidates, ctx);
  return ctx;
}

function termsOf(a: ArticleSummary, ctx: Ctx): Set<string> {
  let set = ctx.terms.get(a);
  if (!set) {
    set = rawTerms(a);
    ctx.terms.set(a, set);
  }
  return set;
}

/** 記事の名前（見出しの固有名詞と、英字の名前のタグ）。両方の記事の名前に出る語は、企業・製品・話題が同じ手がかり */
function namesOf(a: ArticleSummary, ctx: Ctx): Set<string> {
  let set = ctx.names.get(a);
  if (!set) {
    const tagNames = (a.tags ?? []).map(norm).filter((w) => /^[a-z][a-z0-9.+\- ]+$/.test(w) && w.length >= 2 && !NAME_STOP.has(w) && !GENERIC.has(w));
    set = new Set([...titleTerms(a.title), ...tagNames]);
    ctx.names.set(a, set);
  }
  return set;
}

/** 地震・津波の速報（気象庁の型の記事）。それ以外の記事の関連に混ぜない（逆も） */
export function isDisasterArticle(a: Pick<ArticleSummary, "slug" | "title" | "channelSlug">): boolean {
  return /^(?:quake|tsunami)-/.test(a.slug) || a.channelSlug === "jma" || /^【(?:地震|津波)/.test(a.title ?? "");
}

const urlKey = (url?: string) => (url ?? "").trim().toLowerCase().replace(/[?#].*$/, "").replace(/\/$/, "");

/** 同じ発表の重複記事（どちらかがもう一方の duplicateOf / angleOf か、同じ出典URL） */
function isSameStory(a: ArticleSummary, b: ArticleSummary): boolean {
  if (a.duplicateOf && (a.duplicateOf === b.slug || a.duplicateOf === b.duplicateOf)) return true;
  if (b.duplicateOf && b.duplicateOf === a.slug) return true;
  const ua = urlKey(a.sourceUrl);
  return Boolean(ua) && ua === urlKey(b.sourceUrl);
}

export type Similarity = { score: number; shared: string[]; sameCompany: boolean; sameName: boolean; strong: boolean };

/**
 * 2つの記事の話題の重なり。shared は重なった具体的な語、score は語の珍しさ（IDF）の合計。
 * strong（同じ話題と言える）: 具体的な語が2つ以上重なる、または両方の名前（見出しの固有名詞・英字のタグ）に同じ名前が出る、
 * または同じ企業（companies.ts）で語が1つ以上重なる。分野の語1つ（「売上高」「PDF」「ネット」）だけでは同じ話題としない
 */
export function similarity(a: ArticleSummary, b: ArticleSummary, candidates: ArticleSummary[], companySlugOf?: CompanyOf): Similarity {
  const ctx = contextOf(candidates);
  const ta = termsOf(a, ctx);
  const tb = termsOf(b, ctx);
  const na = namesOf(a, ctx);
  const nb = namesOf(b, ctx);
  const shared: string[] = [];
  let score = 0;
  let sameName = false;
  for (const w of ta) {
    if (!tb.has(w)) continue;
    const df = Math.max(ctx.df.get(w) ?? 1, 1);
    const name = na.has(w) && nb.has(w);
    // 多くの記事に付く語は数えない。ただし両方の見出しに出る名前（OpenAI など記事の多い会社）は数える
    if (df > ctx.cap && !name) continue;
    shared.push(w);
    score += Math.log(ctx.n / df);
    if (name) sameName = true;
  }
  const ca = companySlugOf?.(a);
  const sameCompany = Boolean(ca && ca !== "other" && ca === companySlugOf?.(b));
  const strong = shared.length >= 2 || sameName || (shared.length >= 1 && sameCompany);
  return { score, shared, sameCompany, sameName, strong };
}

function candidateOk(article: ArticleSummary, item: ArticleSummary): boolean {
  if (item.slug === article.slug) return false;
  if (isDisasterArticle(item) !== isDisasterArticle(article)) return false;
  return !isSameStory(article, item);
}

const DAY = 86_400_000;

/** 新しい記事を少し上に（同じくらい関係があるときの順番。関係の薄い新着を上げるほどは効かない） */
function freshness(item: ArticleSummary, now: number): number {
  const t = Date.parse(item.publishedAt || item.generatedAt || "");
  if (!Number.isFinite(t)) return 0.7;
  const days = Math.max(0, (now - t) / DAY);
  return 0.7 + 0.3 / (1 + days / 30);
}

/**
 * 関連記事（記事末の一覧）。話題の重なりが基準に届く記事だけを、重なりの大きい順・新しい順に最大 limit 本。
 * 記事を作るときに文脈として使った過去記事（relatedSlugs）も、同じ基準を満たすときだけ先に出す。
 */
export function relatedArticles(
  article: ArticleSummary,
  candidates: ArticleSummary[],
  companySlugOf?: CompanyOf,
  limit = 4,
  now = Date.now(),
): ArticleSummary[] {
  const bySlug = new Map(candidates.map((a) => [a.slug, a]));
  const picked: ArticleSummary[] = [];
  for (const slug of article.relatedSlugs ?? []) {
    const item = bySlug.get(slug);
    if (!item || picked.includes(item) || !candidateOk(article, item)) continue;
    if (!similarity(article, item, candidates, companySlugOf).strong) continue;
    picked.push(item);
    if (picked.length >= limit) return picked;
  }
  const scored = candidates
    .filter((item) => !picked.includes(item) && candidateOk(article, item))
    .map((item) => ({ item, sim: similarity(article, item, candidates, companySlugOf) }))
    .filter((x) => x.sim.strong)
    .map((x) => ({
      item: x.item,
      rank: (x.sim.score + (x.sim.sameCompany || x.sim.sameName ? 1.5 : 0) + (x.item.genreSlug === article.genreSlug ? 0.5 : 0)) * freshness(x.item, now),
    }))
    .sort((a, b) => b.rank - a.rank || Date.parse(b.item.publishedAt) - Date.parse(a.item.publishedAt));
  for (const { item } of scored) {
    if (picked.length >= limit) break;
    picked.push(item);
  }
  return picked;
}

/**
 * 「次に読むならこれ」。1. 同じ話題のまとめ記事（全体像） 2. 同じ話題の記事（具体的な語が2つ以上重なる） 3. 同じ企業の発表（同じ企業・同じ名前） の順。
 * 「同じ話題の記事」の札は、同じ企業か同じ製品・話題の語が重なる記事にだけ付ける（ジャンルや「社会・行政」のような分野の語だけでは付けない）。
 */
export function nextReads(article: ArticleSummary, candidates: ArticleSummary[], companySlugOf: CompanyOf, limit = 3, now = Date.now()): NextRead[] {
  const picked: NextRead[] = [];
  const used = new Set<string>([article.slug]);
  const add = (item: ArticleSummary | undefined, reason: string) => {
    if (!item || used.has(item.slug) || picked.length >= limit || !candidateOk(article, item)) return;
    used.add(item.slug);
    picked.push({ article: item, reason });
  };
  if (article.roundupSlug) add(candidates.find((a) => a.slug === article.roundupSlug), "この話題の全体像");
  const sims = candidates
    .filter((item) => candidateOk(article, item))
    .map((item) => ({ item, sim: similarity(article, item, candidates, companySlugOf) }));
  // 「同じ話題の記事」: 具体的な語が2つ以上重なる記事。名前（企業）だけが重なる記事は「同じ企業の発表」
  const topical = sims
    .filter((x) => x.sim.shared.length >= 2)
    .sort((a, b) => b.sim.score * freshness(b.item, now) - a.sim.score * freshness(a.item, now));
  for (const { item } of topical) add(item, "同じ話題の記事");
  const sameCompany = sims
    .filter((x) => x.sim.strong && (x.sim.sameCompany || x.sim.sameName))
    .sort((a, b) => Date.parse(b.item.publishedAt) - Date.parse(a.item.publishedAt));
  for (const { item } of sameCompany) add(item, "同じ企業の発表");
  return picked;
}
