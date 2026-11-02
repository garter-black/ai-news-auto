import fs, { hasVirtualFs } from "@/lib/content-fs";
import path from "node:path";
import type { Verdict } from "@/lib/verdict";
import type { WorkMoney } from "@/lib/work-money";
import { buildIndex, derivedOf, INDEX_VERSION, isArticleLike, withDefaults } from "@/lib/article-index.mjs";

export type TimetableItem = {
  time: string;
  label: string;
};

/** affiliate_links.json との照合でパイプラインが自動挿入する */
export type AffiliateLink = {
  name: string;
  label: string;
  url: string;
  note?: string;
  /** ASPの表示計測用ピクセル（1x1画像） */
  pixel?: string;
};

/** まとめ記事が統合した元記事 */
export type RoundupSource = {
  slug: string;
  title: string;
  publishedAt: string;
  channel: string;
};

/** 続報で既存記事に足した章（scripts/followup.py） */
export type ArticleUpdate = {
  at: string;
  title: string;
  sourceUrl: string;
  sourceName: string;
};

/**
 * 記事の写真の出典（scripts/images.py）。
 * source=元記事の写真 / stock=フリー素材（CC0・PD・CC BY）/ ai=Workers AI で生成したイメージ
 */
export type ImageCredit = {
  /** official: 公式発表（自社サイト・プレスリリース配信）の写真を縮小して自前で配信。source: 元記事の写真への直リンク（旧方式） */
  kind: "source" | "official" | "stock" | "ai";
  provider?: string;
  /** official: 写真を出した会社・団体の名前 */
  org?: string;
  /** official: プレスリリース配信サービスの名前（PR TIMES など。自社サイトの発表なら無し） */
  via?: string;
  /** official: 保存した JPEG の大きさ（幅は最大 1200px） */
  width?: number;
  height?: number;
  title?: string;
  creator?: string;
  creatorUrl?: string;
  license?: string;
  licenseUrl?: string;
  sourceUrl?: string;
  model?: string;
};

export type Article = {
  slug: string;
  /** "roundup" は複数記事を統合した横断まとめ。省略時は通常記事 */
  type?: "roundup";
  /** まとめ記事が統合した元記事（type === "roundup" のとき）。
   *  web 記事（話題のニュース・続報あり）では {title, url, source} の出典一覧が入るが、画面では使わない */
  sources?: RoundupSource[];
  /** 通常記事から、自分を含むまとめ記事への逆リンク */
  roundupSlug?: string;
  roundupTitle?: string;
  title: string;
  /** 検索結果用の短い題名（<title>）。無ければ title を使う */
  seoTitle?: string;
  /** 記事冒頭のリード文 */
  lede: string;
  description: string;
  keywords: string[];
  tags: string[];
  highlights: string[];
  timetable: TimetableItem[];
  /** 「使えるか」の判定（まとめ記事・古い記事には無いことがある） */
  verdict?: Verdict | null;
  /** 「仕事・お金にどう活かせる？」欄（2026-11-02 以降の記事だけ。lib/work-money.ts） */
  workMoney?: WorkMoney | null;
  affiliates: AffiliateLink[];
  body: string;
  /** 「かんたんに読む」版（専門用語なしの短い解説）。無い記事は切り替えを出さない */
  easy?: string;
  channel: string;
  channelId: string;
  channelSlug: string;
  /** ジャンル（AI・テクノロジー / 不動産 …） */
  genre: string;
  genreSlug: string;
  /** ジャンルごとの免責文（不動産は投資勧誘でない旨を含む） */
  disclaimer: string;
  /** 記事の元ネタ。省略時は "video"（YouTube）。"web" は公式ブログ・プレスリリース */
  sourceType?: "video" | "web";
  /** sourceType === "web" のときの元記事URLと発信元名 */
  sourceUrl?: string;
  sourceName?: string;
  /** 検索に出すかを手動で決める（無ければ lib/indexing.ts の基準） */
  indexable?: boolean;
  /** web 記事では空文字 */
  videoId: string;
  videoUrl: string;
  /** web 記事では元記事の題名 */
  videoTitle: string;
  publishedAt: string;
  /** サイソクに載せた日時（作成。続報を足しても変えない） */
  generatedAt: string;
  /** 続報を足した最後の日時 */
  updatedAt?: string;
  /** 公式発表の日時（web 記事で取れたときだけ） */
  sourcePublishedAt?: string;
  /** 同じ発表の重複記事のとき、代表の記事の slug（scripts/story.py が付ける。noindex・canonical は代表へ） */
  duplicateOf?: string;
  /** 意図して別の角度から書いた解説のとき、元の記事の slug（重複にしないがニュース用サイトマップには載せない） */
  angleOf?: string;
  /** 発表から48時間以上たってから作った記事（速報ではない。ニュース用サイトマップに載せない） */
  late?: boolean;
  /** 公開前の事実確認の点数（100点満点） */
  qualityScore?: number;
  /** 続報の履歴（古い順） */
  updates?: ArticleUpdate[];
  /** 「これまでの流れ」で文脈に使った過去記事の slug */
  relatedSlugs?: string[];
  thumbnail: string;
  /** 写真の出典。stock・ai のときは記事ページに「写真はイメージです」とクレジットを出す */
  imageCredit?: ImageCredit;
  model: string;
  /** 主要企業の公式発表の速報（scripts/web_articles.py が付け、デプロイ直後に SNS へ即時投稿する） */
  breaking?: boolean;
  /** 公式発表から掲載までの分数（デプロイの約3分を含む目安）。発表の時刻が確かで60分以内のときだけ入る */
  speedMinutes?: number;
  /** 地震・津波の記事（scripts/quake.py。気象庁の電文を決まった書式で掲載。LLM を使わない） */
  disaster?: boolean;
  /** 写真・イメージ画像を付けない（地震・津波の記事） */
  noPhoto?: boolean;
};

/** 本文から計算する、一覧で使う値（lib/article-index.mjs の derivedOf） */
export type ArticleDerived = {
  /** 本文の文字数（読了時間・従来の外部配信選定。検索可否は短さだけでは決めない） */
  bodyLength: number;
  /** 「かんたんに読む」版があるか */
  hasEasy: boolean;
  /** 「日本で使える？無料？料金・使い方」の見出しがあるか（検索に出す条件） */
  hasJapanValue: boolean;
  /** 同じ発表の重複記事のうち代表でない（索引を作るときに付く。記事ページ単体では付かない） */
  duplicate?: boolean;
  /** 「かんたん」版の「ひとことで言うと」段落（無ければ空。表示側でリード文に戻す） */
  easyGist: string;
  /** まとめ記事が統合した本数 */
  sourceCount: number;
  /** 本文に自動挿入された提携リンクの名前（「試すなら」枠の話題選び） */
  affiliateNames: string[];
};

/** 一覧・関連記事・サイトマップ・カード用の軽い記事データ（content/index.json の1件） */
export type ArticleSummary = Pick<
  Article,
  | "slug"
  | "type"
  | "roundupSlug"
  | "roundupTitle"
  | "title"
  | "seoTitle"
  | "lede"
  | "description"
  | "keywords"
  | "tags"
  | "highlights"
  | "verdict"
  | "channel"
  | "channelId"
  | "channelSlug"
  | "genre"
  | "genreSlug"
  | "sourceType"
  | "sourceUrl"
  | "sourceName"
  | "sourcePublishedAt"
  | "indexable"
  | "duplicateOf"
  | "angleOf"
  | "late"
  | "videoId"
  | "videoUrl"
  | "publishedAt"
  | "generatedAt"
  | "updatedAt"
  | "relatedSlugs"
  | "thumbnail"
  | "imageCredit"
  | "speedMinutes"
> &
  ArticleDerived;

/** 記事ページ用の全データ（本文つき）。一覧用の値も持つので ArticleSummary として扱える */
export type FullArticle = Article & ArticleDerived;

// 記事のファイルの場所。path.join(process.cwd(), "content") のように書くと、Turbopack のファイル追跡が
// content/ 全体（記事の本文。記事数に比例して増える）を、この部品を使う全ページのサーバー関数に同梱してしまう。
// そのため文字列で組み立て、fs の呼び出しにも turbopackIgnore を付けて追跡させない。
// 何を同梱するかは next.config.ts の outputFileTracingIncludes で明示している
function contentPath(...parts: string[]): string {
  return [process.cwd(), "content", ...parts].join(path.sep);
}
const ARTICLES_DIR = contentPath("articles");
const INDEX_FILE = contentPath("index.json");

function isArticle(value: unknown): value is Article {
  return isArticleLike(value);
}

function normalize(parsed: Article): FullArticle {
  const article = withDefaults(parsed) as Article;
  return { ...article, ...(derivedOf(article) as ArticleDerived) };
}

/**
 * 一覧用の索引を読む。本番はビルド前に作った content/index.json（scripts/build_index.mjs）。
 * 開発中は記事が増えても反映されるよう、毎回 content/articles から作る（索引が無いときも同じ）。
 * サーバー関数には content/articles を同梱しない（記事ページ・カバー以外）ので、索引が頼り。
 */
function readIndex(): ArticleSummary[] {
  if (process.env.NODE_ENV !== "development") {
    try {
      const parsed = JSON.parse(fs.readFileSync(/* turbopackIgnore: true */ INDEX_FILE, "utf8")) as { version?: number; articles?: unknown };
      if (parsed.version === INDEX_VERSION && Array.isArray(parsed.articles)) {
        return parsed.articles as ArticleSummary[];
      }
      console.warn("[articles] content/index.json is outdated; rebuilding from content/articles");
    } catch {
      console.warn("[articles] content/index.json not found; rebuilding from content/articles");
    }
  }
  return buildIndex(ARTICLES_DIR) as ArticleSummary[];
}

/** 動画から作った記事か（web 記事・videoId が空の記事は false） */
export function isVideo(article: ArticleSummary): boolean {
  return article.sourceType !== "web" && article.videoId !== "";
}

/** 公式ブログ・プレスリリースから作った記事か */
export function isWeb(article: ArticleSummary): boolean {
  return article.sourceType === "web";
}

/** 出典の表示名（web は発信元名、動画はチャンネル名） */
export function sourceLabel(article: ArticleSummary): string {
  return (isWeb(article) && article.sourceName) || article.channel;
}

/** 出典へのリンク（web は元記事、動画は YouTube） */
export function sourceHref(article: ArticleSummary): string {
  return isWeb(article) ? article.sourceUrl || "" : article.videoUrl;
}

// モジュールスコープでキャッシュ（ビルド中に何百回も呼ばれるため）
let cache: ArticleSummary[] | null = null;
let bySlug: Map<string, ArticleSummary> | null = null;

/** 全記事の一覧用データ（新しい順）。本文は含まない。本文が要るときは getArticle */
export function getAllArticles(): ArticleSummary[] {
  if (cache === null) cache = readIndex();
  return cache;
}

/** 一覧用データを slug で引く（関連記事・比較ガイドのリンク用。本文は読まない） */
export function getArticleSummary(slug: string): ArticleSummary | undefined {
  if (bySlug === null) bySlug = new Map(getAllArticles().map((a) => [a.slug, a]));
  return bySlug.get(slug);
}

/** 横断まとめ記事だけを新しい順に返す（トップのハブ枠用） */
export function getRoundups(): ArticleSummary[] {
  return getAllArticles().filter((a) => a.type === "roundup");
}

const fullCache = new Map<string, FullArticle | null>();

/**
 * 記事1本の全データ（本文つき）。ファイル名＝slug の1本だけを読む。
 * content/articles を同梱しているのは記事ページ・カバー画像・API だけ（next.config.ts）なので、
 * それ以外のページからは呼ばないこと（一覧には getAllArticles / getArticleSummary を使う）。
 */
export function getArticle(slug: string): FullArticle | undefined {
  if (fullCache.has(slug)) return fullCache.get(slug) ?? undefined;
  const one = readOne(slug);
  // ビルド中は同じ記事を何度も読むので覚えておく（実行時は1関数で数本しか読まない）
  // Cloudflare では先読みした記事だけが読めるので覚えない（cloudflare/worker.ts）
  if (fullCache.size < 2000 && !hasVirtualFs()) fullCache.set(slug, one ?? null);
  return one;
}

function readOne(slug: string): FullArticle | undefined {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(slug)) return undefined;
  try {
    const parsed: unknown = JSON.parse(fs.readFileSync(/* turbopackIgnore: true */ `${ARTICLES_DIR}${path.sep}${slug}.json`, "utf8"));
    return isArticle(parsed) && parsed.slug === slug ? normalize(parsed) : undefined;
  } catch {
    return undefined;
  }
}

/** ビルド時に静的化する記事の数（新しい順）。残りは初回アクセス時に生成してキャッシュする */
export const PRERENDER_LIMIT = 150;

/** 最終更新日時（続報があれば updatedAt。サイトマップ・構造化データ用） */
export function lastModifiedOf(article: ArticleSummary): string {
  return article.updatedAt || article.generatedAt || article.publishedAt;
}

/** 直近 hours 時間以内に続報を足した記事か（一覧の「更新」バッジ） */
/** 旧 speedMinutes は生成時刻に固定3分を足した推定で、公開確認の実測ではない。
 * 表示を再開する場合は、照合済み出典日時・公開確認記録・母数/欠測を伴う別の証拠を使う。
 * 既存データと呼出しは保持するが、推定値だけを「掲載まで○分」と表示しない。
 */
export function speedLabel(_article: Pick<Article, "speedMinutes">): string | null {
  return null;
}

export function isRecentlyUpdated(article: ArticleSummary, hours = 48): boolean {
  if (!article.updatedAt) return false;
  const age = Date.now() - Date.parse(article.updatedAt);
  return age >= 0 && age < hours * 3_600_000;
}

export function getRelatedArticles(article: ArticleSummary, limit = 4): ArticleSummary[] {
  // 記事を作るときに文脈として使った過去記事を先に出す
  const picked = (article.relatedSlugs ?? [])
    .filter((slug) => slug !== article.slug)
    .map((slug) => getArticleSummary(slug))
    .filter((a): a is ArticleSummary => Boolean(a))
    .slice(0, limit);
  const used = new Set([article.slug, ...picked.map((a) => a.slug)]);
  const tags = new Set(article.tags);
  const rest = getAllArticles()
    .filter((a) => !used.has(a.slug))
    .map((a) => ({
      article: a,
      score:
        a.tags.filter((t) => tags.has(t)).length +
        (a.channelSlug === article.channelSlug ? 1 : 0) +
        // 別ジャンルの記事が関連記事に混ざらないよう強めに重み付けする
        (a.genreSlug === article.genreSlug ? 5 : 0),
    }))
    .sort((a, b) => b.score - a.score)
    .slice(0, limit - picked.length)
    .map((x) => x.article);
  return [...picked, ...rest];
}

/* ------------------------------------------------------------------ */
/* pSEO 用の集約（タグ別 / チャンネル別ページを自動生成するための材料）   */
/* ------------------------------------------------------------------ */

export type Facet = {
  slug: string;
  label: string;
  count: number;
};

/**
 * タグの識別子。**URLエンコードしない**のが重要。
 * generateStaticParams に渡す値は「デコード済み」でなければならず、
 * ここでエンコードすると Next.js が再度エンコードして二重エンコードになり、
 * 日本語タグのページが 404 になる。
 */
export function tagSlug(tag: string): string {
  return tag.trim().toLowerCase().replace(/\s+/g, "-");
}

/** リンクの href には（ブラウザ互換のため）エンコードした値を使う */
export function tagHref(tag: string): string {
  return `/tags/${encodeURIComponent(tagSlug(tag))}`;
}

/**
 * タグの集計。onlyAi を付けると AI ジャンルの記事だけで数える
 * （サイトはAIに一点集中しているので、導線に不動産・投資のタグを出さない）。
 */
export function getAllTags(onlyAi = false): Facet[] {
  const map = new Map<string, Facet>();
  const source = getAllArticles().filter((a) => !onlyAi || a.genreSlug === "ai");
  for (const article of source) {
    for (const tag of article.tags) {
      const slug = tagSlug(tag);
      if (!slug) continue;
      const found = map.get(slug);
      if (found) found.count += 1;
      else map.set(slug, { slug, label: tag, count: 1 });
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}

export function getArticlesByTag(slug: string): ArticleSummary[] {
  // Next.js が渡す params はデコード済みだが、念のため両方で照合する
  const decoded = safeDecode(slug);
  return getAllArticles().filter((a) =>
    a.tags.some((t) => {
      const s = tagSlug(t);
      return s === slug || s === decoded;
    }),
  );
}

/**
 * 表示用のタグ名。params は環境によって %XX のまま渡ることがあるため、
 * 必ずデコードしてから照合する（しないと見出しに %E4%B8%8D... が出る）。
 */
export function getTagLabel(slug: string): string {
  const decoded = safeDecode(slug);
  const found = getAllTags().find(
    (t) => t.slug === slug || t.slug === decoded,
  );
  return found?.label ?? decoded;
}

function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** ジャンル一覧（記事が1本でもあるものだけ） */
export function getAllGenres(): Facet[] {
  const map = new Map<string, Facet>();
  for (const article of getAllArticles()) {
    if (!article.genreSlug) continue;
    const found = map.get(article.genreSlug);
    if (found) found.count += 1;
    else
      map.set(article.genreSlug, {
        slug: article.genreSlug,
        label: article.genre,
        count: 1,
      });
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}

export function getArticlesByGenre(genreSlug: string): ArticleSummary[] {
  return getAllArticles().filter((a) => a.genreSlug === genreSlug);
}

/** そのジャンルに属するチャンネル一覧 */
export function getChannelsByGenre(genreSlug: string): Facet[] {
  const map = new Map<string, Facet>();
  for (const article of getArticlesByGenre(genreSlug)) {
    const found = map.get(article.channelSlug);
    if (found) found.count += 1;
    else
      map.set(article.channelSlug, {
        slug: article.channelSlug,
        label: article.channel,
        count: 1,
      });
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}

export function getAllChannels(): Facet[] {
  const map = new Map<string, Facet>();
  for (const article of getAllArticles()) {
    if (!article.channelSlug) continue;
    const found = map.get(article.channelSlug);
    if (found) found.count += 1;
    else
      map.set(article.channelSlug, {
        slug: article.channelSlug,
        label: article.channel,
        count: 1,
      });
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}

export function getArticlesByChannel(channelSlug: string): ArticleSummary[] {
  return getAllArticles().filter((a) => a.channelSlug === channelSlug);
}

/** "2026/10/27 09:30"（日本時間） */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Tokyo",
  }).format(date);
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Tokyo",
  }).format(date);
}

/**
 * 独自カバー画像（/cover/[slug]）のパス。AI記事だけ生成する（不動産・投資は検索対象外のため）。
 * 生成対象外なら null を返すので、元動画のサムネイルにフォールバックする。
 */
export function coverPath(article: ArticleSummary): string | null {
  // 全ジャンルを検索対象にしたので、共有用の画像も全記事で作る
  return `/cover/${article.slug}`;
}

export type NextRead = { article: ArticleSummary; reason: string };
const COMPANY_TAGS = ["OpenAI", "Microsoft", "Google", "NVIDIA", "Meta", "Anthropic", "Hugging Face", "DeepSeek", "ChatGPT", "Gemini", "Claude"];

/**
 * 「次に読むならこれ」。記事を読み終えた人に、もう1本だけ読んでもらうための候補。
 * 1. 同じ話題のまとめ記事（全体像） 2. 同じ企業の新しい発表 3. タグが重なる発表 の順に選ぶ。
 */
export function getNextReads(article: ArticleSummary, companySlugOf: (a: ArticleSummary) => string, limit = 3): NextRead[] {
  const all = getAllArticles().filter((a) => a.slug !== article.slug && a.genreSlug === article.genreSlug);
  const picked: NextRead[] = [];
  const used = new Set<string>();
  const add = (candidate: ArticleSummary | undefined, reason: string) => {
    if (!candidate || used.has(candidate.slug) || picked.length >= limit) return;
    used.add(candidate.slug);
    picked.push({ article: candidate, reason });
  };

  if (article.roundupSlug) add(all.find((a) => a.slug === article.roundupSlug), "この話題の全体像");

  const company = companySlugOf(article);
  const sameCompany = all.filter((a) => companySlugOf(a) === company && company !== "other");

  // 「生成AI」のような全記事共通タグだけでは同じ話題と言えない。
  const genericTags = new Set(["AI", "AIツール", "AIニュース", "生成AI", "人工知能", "テクノロジー", "AIエージェント", "LLM", ...COMPANY_TAGS]);
  const tags = new Set(article.tags.filter((tag) => !genericTags.has(tag)));
  const keywords = new Set(article.keywords.filter((word) => word.length >= 3 && !genericTags.has(word) && !/^(使い方|料金|無料|日本語|とは|開発|アップデート)$/i.test(word)).map((word) => word.toLowerCase()));
  const byTags = all
    .map((a) => ({ a, n: a.tags.filter((t) => tags.has(t)).length + 2 * a.keywords.filter((word) => keywords.has(word.toLowerCase())).length }))
    .filter((x) => x.n > 0)
    .sort((x, y) => y.n - x.n || Date.parse(y.a.publishedAt) - Date.parse(x.a.publishedAt));
  for (const { a } of byTags) add(a, "同じ話題の記事");
  for (const a of sameCompany) add(a, "同じ企業の発表");
  return picked;
}
