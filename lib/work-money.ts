/**
 * 「仕事・お金にどう活かせる？」欄（記事 JSON の workMoney）。scripts/persona.py の clean_work_money が作る。
 * 2026-11-02 以降に作った記事・解説にだけある。無い記事・壊れたデータでは欄を出さない。
 */
export type WorkMoney = {
  /** 仕事・お金への関係の強さ */
  relevance: "high" | "medium" | "low";
  /** 1〜3個の短い文（元記事で確かめられる事実だけ。品質ゲートの事実確認の対象） */
  points: string[];
};

export const WORK_MONEY_HEADING = "仕事とお金への影響";

export const RELEVANCE_LABEL: Record<WorkMoney["relevance"], string> = {
  high: "影響：大きい",
  medium: "影響：一部の人",
  low: "影響：小さい",
};

/** JSON の値を検証して返す（表示しないときは null） */
export function workMoneyOf(value: unknown): WorkMoney | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as { relevance?: unknown; points?: unknown };
  const points = Array.isArray(raw.points)
    ? raw.points.filter((p): p is string => typeof p === "string" && p.trim().length > 0).map((p) => p.trim()).slice(0, 3)
    : [];
  if (points.length === 0) return null;
  const relevance = raw.relevance === "high" || raw.relevance === "low" ? raw.relevance : "medium";
  return { relevance, points };
}
