/**
 * 朝のまとめ（/digest/<YYYYMMDD>）のシェア画像のデザイン。ここを1か所変えるだけで切り替わる。
 *   "A" 写真3枚のコラージュ（報道テロップ型）
 *   "B" トップ記事を大きく＋残り2本を横に縦並び（写真に文字を重ねる旧案）
 *   "B1" 紺の地。左にトップ記事（写真の下に見出し）、右に2本を［写真｜見出し］。写真は切らず、文字は写真に重ねない
 *   "B2" 明るい地。上にトップ記事［写真｜見出し］、下に2本を横に並べる。写真は切らず、文字は写真に重ねない
 *   "C" AI生成のイラスト背景＋番号つきの見出し一覧（号ごとに1枚だけ scripts/digest_page.py が作って KV に置く）
 * scripts/digest_page.py もこの値を読む（C のときだけ背景を作る）。
 */
export type DigestDesign = "A" | "B" | "B1" | "B2" | "C";

export const DIGEST_DESIGNS: readonly DigestDesign[] = ["A", "B", "B1", "B2", "C"];

export const DIGEST_DESIGN: DigestDesign = "A";
