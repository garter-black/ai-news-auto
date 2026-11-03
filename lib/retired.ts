import retiredJson from "@/content/retired.json";
import { getAllArticles, type ArticleSummary } from "@/lib/articles";

/**
 * 取り下げた記事（content/retired.json。scripts/retire_articles.py が書く）の 410 ページの材料（2026-11-03 オーナー決定・台帳 W22）。
 * ページは app/gone/[slug]/page.tsx。cloudflare/worker.ts が /articles/<取り下げた slug> を受けたとき、このページを描いて 410 で返す
 * （サイトのヘッダー・フッター・色のまま。前は素の HTML で、壊れたサイトに見えた）。
 */
export type RetiredEntry = { title?: string; channel?: string; reason?: string; redirect?: string; retiredAt?: string };

const RETIRED = ((retiredJson as { articles?: Record<string, RetiredEntry> }).articles ?? {}) as Record<string, RetiredEntry>;

export function getRetired(slug: string): RetiredEntry | undefined {
  return Object.prototype.hasOwnProperty.call(RETIRED, slug) ? RETIRED[slug] : undefined;
}

/** 取り下げた記事の slug の一覧（サイト内検索の索引から外す。lib/search-index.mjs の searchExclusion） */
export function retiredSlugs(): string[] {
  return Object.keys(RETIRED);
}

// どの記事にも出てくる言葉は関連の手がかりにしない
const STOP = new Set([
  "発表", "公開", "開始", "提供", "新機能", "機能", "記事", "解説", "まとめ", "日本", "対応", "搭載", "開催", "発売", "追加", "導入",
  "実施", "最新", "方針", "注意", "注意喚起", "サービス", "について", "とは", "使える", "無料", "料金", "東京", "全国", "国内",
]);
const TOKEN_RE = /[A-Za-z][A-Za-z0-9.+-]{1,}|[0-9]+[A-Za-z]+|[ァ-ヴー]{2,}|[一-龠々]{2,}/g;

/** 題名の手がかりの言葉（英字・カタカナは重く、漢字は軽く） */
export function titleTokens(title: string): { word: string; weight: number }[] {
  const seen = new Set<string>();
  const out: { word: string; weight: number }[] = [];
  for (const m of (title ?? "").normalize("NFKC").matchAll(TOKEN_RE)) {
    const word = m[0].toLowerCase();
    if (STOP.has(word) || seen.has(word)) continue;
    seen.add(word);
    out.push({ word, weight: /^[a-z0-9]/.test(word) || /^[ァ-ヴー]+$/.test(word) ? 2 : 1 });
  }
  return out;
}

/**
 * 取り下げた記事の代わりに読める、今も載っている記事（2〜4本）。
 * 同じ発信元（channel）と題名の言葉の重なりで選び、手がかりが足りなければ新しい記事で埋める。
 */
export function relatedLiveArticles(entry: RetiredEntry, limit = 4, articles: ArticleSummary[] = getAllArticles()): ArticleSummary[] {
  const live = articles.filter((a) => a.type !== "roundup" && !a.duplicate);
  const tokens = titleTokens(entry.title ?? "");
  const scored = live
    .map((a) => {
      const hay = `${a.title} ${(a.tags ?? []).join(" ")}`.normalize("NFKC").toLowerCase();
      const words = tokens.reduce((n, t) => n + (hay.includes(t.word) ? t.weight : 0), 0);
      return { a, score: words + (entry.channel && a.channelSlug === entry.channel ? 3 : 0) };
    })
    .filter((x) => x.score >= 2)
    .sort((x, y) => y.score - x.score || Date.parse(y.a.publishedAt) - Date.parse(x.a.publishedAt))
    .map((x) => x.a);
  const picked = scored.slice(0, limit);
  if (picked.length < 2) {
    // 近い記事が無いときは、新しい記事（AI・ビジネスを先に）で埋める
    const used = new Set(picked.map((a) => a.slug));
    const fill = live
      .filter((a) => !used.has(a.slug))
      .sort((x, y) => Number(y.genreSlug === "ai") - Number(x.genreSlug === "ai") || Date.parse(y.publishedAt) - Date.parse(x.publishedAt));
    picked.push(...fill.slice(0, Math.max(0, Math.min(limit, 3) - picked.length)));
  }
  return picked;
}
