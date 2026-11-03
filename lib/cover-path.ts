/** 独自カバー画像の URL（クライアントの部品からも読めるよう、依存のない小さなファイルに置く） */

/**
 * カバー画像の版。デザイン（ラベルの文言など）を変えたら1つ上げる。
 * /cover/<slug> はブラウザ・CDN で7日（immutable）使い回されるので、URL に ?v= を付けて新しい画像を取りに行かせる。
 * 2: 「速報」は速報の記事だけ、ほかは「ニュース」（2026-11-03 の画面の点検 B）
 * 3: 画像の題名を検索向けの seoTitle から記事の題名（lib/headline.ts の articleTitle）に替えた（2026-11-03 の点検 10）
 */
export const COVER_VERSION = 3;

/** 題名の短いハッシュ（題名を直したら URL が変わり、ブラウザ・SNS・エッジに残った古い画像を使わせない） */
export function titleHash(title: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < title.length; i++) {
    h ^= title.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(36);
}

/**
 * 独自カバー画像（/cover/[slug]）のパス（版＋題名のハッシュつき）。全記事で作る。
 */
export function coverPath(article: { slug: string; title?: string | null }): string {
  const title = (article.title ?? "").trim();
  return `/cover/${article.slug}?v=${COVER_VERSION}${title ? `-${titleHash(title)}` : ""}`;
}

/**
 * 画像に赤い「速報」を付けてよい記事か。速報（主要企業の公式発表の速報。article.breaking）か、
 * 公式発表から60分以内に載せた記事（speedMinutes が入る）だけ。ほかは「ニュース」（2026-11-03 の画面の点検 B）
 */
export function isFastNews(article: { breaking?: boolean | null; speedMinutes?: number | null; type?: string | null }): boolean {
  if (article.type === "roundup") return false;
  return Boolean(article.breaking) || typeof article.speedMinutes === "number";
}
