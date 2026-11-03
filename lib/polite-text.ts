import { professionalSentence, splitSentences } from "@/lib/article-sections";

/**
 * ガイド・比較・選び方のページの文末の「〜しましょう」「〜してみましょう」を、落ち着いた言い方（「〜するとよいでしょう」）にする
 * （2026-11-03 の点検 #15・台帳 W13。記事だけに当てていた A17 をガイドにも広げる）。
 * 言い換えの規則は記事と同じ lib/article-sections.ts の professionalSentence（scripts/section_gate.py と同じ）。事実は変えない。
 */
export function politeText(text: string): string {
  if (!text || !text.includes("ましょう")) return text;
  return text
    .split("\n")
    .map((line) => splitSentences(line).map(professionalSentence).join(""))
    .join("\n");
}

/** JSON のデータ（ガイド）の中の文をすべて politeText にかける。URL・日付など「ましょう」を含まない値はそのまま */
export function politeDeep<T>(value: T): T {
  if (typeof value === "string") return politeText(value) as T;
  if (Array.isArray(value)) return value.map((v) => politeDeep(v)) as T;
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, politeDeep(v)])) as T;
  }
  return value;
}
