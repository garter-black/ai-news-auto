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
