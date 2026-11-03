import type { WorkMoney } from "@/lib/work-money";
import fs from "@/lib/content-fs";
import path from "node:path";
import { getAllArticles, type ArticleSummary } from "@/lib/articles";
import { politeDeep } from "@/lib/polite-text";

/**
 * 使い方ガイド（content/howto/*.json、scripts/evergreen.py が作る。URL は /guides/<slug>）。
 *
 * 「ChatGPT 使い方」「Gemini 無料 どこまで」のように長く検索される疑問に、公式ページだけを根拠に答える。
 * 自動更新の対象は14日ごと（料金などの関連ニュースが出たときはすぐ）に公式ページを確認し直す。
 * 手動編集の対象は確認待ちとして記録し、編集者が確かめるまで本文・確認日（checkedAt）を更新しない。
 */
export type HowtoGuide = {
  slug: string;
  tool: string;
  toolName: string;
  company: string;
  intent: string;
  keyword: string;
  title: string;
  seoTitle: string;
  description: string;
  conclusion: string[];
  intro: string;
  steps: { title: string; body: string; commands?: string[] }[];
  pricing: { plan: string; price: string; note?: string }[];
  freeTier: string[];
  japan: { answer: string; body: string };
  tips: { title: string; body: string }[];
  faq: { q: string; a: string }[];
  /** 「仕事・お金にどう活かせる？」欄（2026-11-02 以降に作り直した解説だけ） */
  workMoney?: WorkMoney | null;
  /** 関連ニュースを見つける正規表現（見出しと短い題名に照合） */
  match: string;
  /** 同じ題材のガイドが複数あるとき、記事の疑問に合うガイドを優先する。未一致なら従来順。 */
  articleIntent?: { match: string; priority?: number };
  /** 関連する製品のまとめページ（content/products）の slug */
  products: string[];
  sources: { url: string; title: string; checkedAt: string }[];
  createdAt: string;
  updatedAt: string;
  /** 公式ページで内容を確かめた日時（確認日） */
  checkedAt: string;
  /** 手動原稿は自動生成せず、期限や関連ニュースを検知したら編集待ちにする。 */
  refreshMode?: "editorial";
  /** 手動編集が必要になった日時。確認が終わるまで本文・確認日とは分けて保持する。 */
  needsReview?: string;
  /** 編集待ちになった理由（確認期限・関連ニュースなど）。 */
  needsReviewReason?: string;
  /** 手動編集した広告・応募記事の開示。設定した記事だけ冒頭に表示する。 */
  disclosure?: string;
  /** 自動生成・別AI照合・定期更新の定型説明に代わる、実際の編集方法。 */
  editorialNote?: string;
  officialAction?: { url: string; label: string };
  /** 回答を読んだあとに案内する選び方ガイド。既存の有効なslugだけ表示する。 */
  decisionGuideSlug?: string;
};

// 文字列で組み立てて、content/ 全体をサーバー関数に同梱させない（lib/articles.ts と同じ理由）
const HOWTO_DIR = [process.cwd(), "content", "howto"].join(path.sep);

let cache: HowtoGuide[] | null = null;

/** 新しい順 */
export function getAllHowtos(): HowtoGuide[] {
  if (cache) return cache;
  if (!fs.existsSync(HOWTO_DIR)) return (cache = []);
  cache = fs
    .readdirSync(HOWTO_DIR)
    .filter((file) => file.endsWith(".json"))
    .flatMap((file) => {
      try {
        // 文末の「〜しましょう」は落ち着いた言い方にして出す（台帳 W13。lib/polite-text.ts）
        const data = politeDeep(JSON.parse(fs.readFileSync(/* turbopackIgnore: true */ `${HOWTO_DIR}${path.sep}${file}`, "utf8")) as HowtoGuide);
        return data.slug && data.steps?.length ? [data] : [];
      } catch {
        return [];
      }
    })
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return cache;
}

export function getHowto(slug: string): HowtoGuide | undefined {
  return getAllHowtos().find((guide) => guide.slug === slug);
}

const newsCache = new Map<string, ArticleSummary[]>();

/** ガイドの題材に関係する AI 記事（新しい順） */
export function howtoArticles(guide: HowtoGuide): ArticleSummary[] {
  const cached = newsCache.get(guide.slug);
  if (cached) return cached;
  let pattern: RegExp;
  try {
    pattern = new RegExp(guide.match);
  } catch {
    return [];
  }
  const articles = guide.match
    ? getAllArticles().filter(
        (article) => article.genreSlug === "ai" && pattern.test(`${article.title} ${article.seoTitle ?? ""}`),
      )
    : [];
  newsCache.set(guide.slug, articles);
  return articles;
}

/** 製品のまとめページから案内する使い方ガイド */
export function howtosForProduct(productSlug: string): HowtoGuide[] {
  return getAllHowtos().filter((guide) => guide.products.includes(productSlug));
}

/** 同じ題材（ツール）のほかのガイド */
export function siblingHowtos(guide: HowtoGuide, limit = 6): HowtoGuide[] {
  return getAllHowtos()
    .filter((other) => other.slug !== guide.slug && other.tool === guide.tool)
    .concat(getAllHowtos().filter((other) => other.slug !== guide.slug && other.tool !== guide.tool))
    .slice(0, limit);
}

/** 記事の見出しに題材が出てくるガイド（記事ページから1行だけ案内する） */
export function howtoForArticle(article: ArticleSummary, guides: HowtoGuide[] = getAllHowtos()): HowtoGuide | undefined {
  const text = `${article.title} ${article.seoTitle ?? ""}`;
  const matches = (pattern: string | undefined, flags = "") => {
    try {
      return Boolean(pattern && new RegExp(pattern, flags).test(text));
    } catch {
      return false;
    }
  };
  let fallback: HowtoGuide | undefined;
  let preferred: HowtoGuide | undefined;
  let highestPriority = -Infinity;
  for (const guide of guides) {
    if (!matches(guide.match)) continue;
    fallback ??= guide;
    if (!matches(guide.articleIntent?.match, "i")) continue;
    const priority = guide.articleIntent?.priority ?? 0;
    // 同点は従来順のまま。キャッシュされたガイド配列は並び替えない。
    if (Number.isFinite(priority) && priority > highestPriority) {
      preferred = guide;
      highestPriority = priority;
    }
  }
  return preferred ?? fallback;
}
