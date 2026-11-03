/**
 * ヘッダー下の1行（components/LiveLine.tsx）の札（2026-11-03 オーナー決定・台帳 W23）。
 * 赤い「速報」は本当の速報だけ。画像の赤い「速報」と同じ判定（lib/cover-path.ts の isFastNews: breaking か公式発表から60分以内）に加え、
 * 載せてから6時間以内のときだけ。ほかは紺の「最新」（「19時間前」の記事に赤い「速報」が付いていた）。
 * 依存のない小さなファイル（クライアントの部品からも読む）。
 */
export const LIVE_FRESH_MS = 6 * 3_600_000;

export type LiveLabel = "速報" | "最新";

export function liveLabel(item: { fast?: boolean; publishedAt: string }, now: number): LiveLabel {
  if (!item.fast) return "最新";
  const t = Date.parse(item.publishedAt);
  return Number.isFinite(t) && now >= t && now - t < LIVE_FRESH_MS ? "速報" : "最新";
}
