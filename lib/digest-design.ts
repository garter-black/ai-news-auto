/**
 * 朝のまとめ（/digest/<YYYYMMDD>）のシェア画像のデザイン。ここを1か所変えるだけで切り替わる。
 *   "A" 写真3枚のコラージュ（報道テロップ型）
 *   "B" トップ記事を大きく＋残り2本を横に縦並び
 *   "C" AI生成のイラスト背景＋番号つきの見出し一覧（号ごとに1枚だけ scripts/digest_page.py が作って KV に置く）
 * scripts/digest_page.py もこの値を読む（C のときだけ背景を作る）。
 */
export type DigestDesign = "A" | "B" | "C";

export const DIGEST_DESIGN: DigestDesign = "A";
