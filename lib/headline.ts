/**
 * 一覧・カードに出す見出し。40字を超える見出しは、最初の区切り（全角の空白・－・｜、日本語のすぐ後ろの半角空白）までの前半だけを出す。
 * 記事ページの見出し（h1）・検索用の題名（seoTitle）・構造化データは全文のまま。
 * 区切りは scripts/headline_normalize.py が全角の空白にそろえている。品質ゲートも40字超を警告する（quality.title_warnings）。
 */
export const DISPLAY_MAX = 40;

/** 最初の区切りまでの前半。短すぎる（12字未満）・かっこが閉じていない・区切りが無いときは null */
export function headOf(title: string): string | null {
  const text = title.trim();
  // 「Agent Compute Engine」の中のような英字どうしの間の空白では切らない
  const head = text.split(/(?<=[^\x00-\x7f])[ 　]|[　－―—｜|]/)[0].trim();
  const balanced = (head.match(/[『「（(]/g) ?? []).length === (head.match(/[』」）)]/g) ?? []).length;
  return head.length >= 12 && head.length < text.length && balanced ? head : null;
}

/** 一覧・カード用の見出し（40字以内はそのまま） */
export function displayHeadline(title: string): string {
  const text = title.trim();
  return text.length > DISPLAY_MAX ? (headOf(text) ?? text) : text;
}

/**
 * 見出しを本見出しと補足（最初の全角の空白のあと）に分ける（2026-11-03 オーナー指摘: 補足は意図した改行として別の行に出す）。
 * かっこの中の空白・前半が短すぎる（8字未満）・後半が短すぎる（3字未満）ときは分けない。
 */
export function splitHeadline(title: string): { main: string; sub: string | null } {
  const text = title.trim();
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if ("「『（(".includes(ch)) depth++;
    else if ("」』）)".includes(ch) && depth > 0) depth--;
    else if (ch === "　" && depth === 0) {
      const main = text.slice(0, i).trim();
      const sub = text.slice(i + 1).trim();
      if (main.length >= 8 && sub.length >= 3) return { main, sub };
      return { main: text, sub: null };
    }
  }
  return { main: text, sub: null };
}
