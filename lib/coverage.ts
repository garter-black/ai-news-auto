import { getAllArticles, type ArticleSummary } from "@/lib/articles";
import { GENRE_NAV_ORDER, genreLabel } from "@/lib/nav";

/**
 * サイトの自己紹介で名前を出してよいジャンル（2026-11-03 オーナー決定・台帳 W14）。
 *
 * 点検で、メニューの 投資・マネー（1本）・エンタメ（1本）・不動産（0本）がほぼ空なのに、説明では
 * 「お金・投資・不動産を扱う」と書いていたことが分かった。説明は実際に扱っている中身に合わせる:
 *  - 軸は「AI・テクノロジーを軸に、仕事とお金に関わるニュース」（site.config.ts の description。いつでも本当）
 *  - そのほかのジャンルは、直近 REGULAR_DAYS 日に REGULAR_MIN 本以上の記事があるもの（定期的に記事がある）だけ名前を出す。
 *    記事が増えれば自然に名前が載り、止まれば消える（説明が実態を追い越さない）。
 * 記事を作る側の決まり（各ジャンルで扱う中身・1日の最低本数）は scripts/genre_focus.py。
 */
export const REGULAR_DAYS = 30;
export const REGULAR_MIN = 5;

function articleTime(a: ArticleSummary): number {
  const t = Date.parse(a.generatedAt || a.publishedAt || "");
  return Number.isFinite(t) ? t : 0;
}

/** 定期的に記事があるジャンル（AI 以外。メニューの並び順） */
export function regularGenres(
  articles: ArticleSummary[] = getAllArticles(),
  now: number = Date.now(),
): { slug: string; label: string; count: number }[] {
  const since = now - REGULAR_DAYS * 24 * 60 * 60 * 1000;
  const counts = new Map<string, number>();
  for (const a of articles) {
    if (!a.genreSlug || a.genreSlug === "ai" || a.type === "roundup") continue;
    if (articleTime(a) < since) continue;
    counts.set(a.genreSlug, (counts.get(a.genreSlug) ?? 0) + 1);
  }
  return GENRE_NAV_ORDER.filter((slug) => slug !== "ai" && (counts.get(slug) ?? 0) >= REGULAR_MIN).map((slug) => ({
    slug,
    label: genreLabel(slug),
    count: counts.get(slug) ?? 0,
  }));
}

/** 「このほか、〇〇・△△のニュースも扱います。」（定期的な記事が無ければ空文字） */
export function alsoCoveredSentence(articles?: ArticleSummary[], now?: number): string {
  const labels = regularGenres(articles, now).map((g) => g.label);
  return labels.length ? `このほか、${labels.join("、")}のニュースも扱います。` : "";
}
