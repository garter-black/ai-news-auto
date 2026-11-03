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

/**
 * 欄の見出し。決まった「仕事とお金への影響」を全部の記事に出さず、ニュースの種類に合った普通の言葉にする
 * （2026-11-03 オーナーの指摘・台帳 A28。種類は scripts/news_types.py の key）
 */
const HEADING_BY_TYPE: Record<string, string> = {
  product: "使う人への影響",
  pricing: "利用者の負担はどう変わるか",
  earnings: "株主・取引先への影響",
  deal: "取引先・利用者への影響",
  filing: "投資家への影響",
  policy: "対象になる人への影響",
  security: "利用者がすること",
  research: "働く人への示唆",
  people: "取引先への影響",
  event: "参加者・利用者への影響",
  sports: "観戦する人への情報",
};

// 種類の無い古い記事はジャンルで決める
const HEADING_BY_GENRE: Record<string, string> = {
  ai: "仕事での使いどころ",
  "tech-gadget": "使う人への影響",
  public: "対象になる人への影響",
  sports: "観戦する人への情報",
  entertainment: "楽しむ人への情報",
};

// 種類の無い古い記事は、題名・タグのはっきりした言葉で種類を推す（決算の記事に「使う人への影響」と出ないように）
const TYPE_HINTS: [RegExp, string][] = [
  [/決算|業績|売上高|営業利益|純利益|配当/, "earnings"],
  [/脆弱性|不正アクセス|流出|障害/, "security"],
  [/買収|提携|協定|出資|合併/, "deal"],
  [/値上げ|値下げ|料金改定|新料金|プラン変更/, "pricing"],
];

export function workMoneyHeading(newsType?: string, genreSlug?: string, title = "", tags: string[] = []): string {
  const hinted = newsType || TYPE_HINTS.find(([re]) => re.test(`${title} ${tags.join(" ")}`))?.[1] || "";
  return HEADING_BY_TYPE[hinted] ?? HEADING_BY_GENRE[genreSlug ?? ""] ?? WORK_MONEY_HEADING;
}

// 事実（数字・英字の名前・「」）を含むか。含まない一般論（「業務効率が上がります」だけ）は記事ごとの中身ではない
const FACT_RE = /[0-9０-９]|[A-Za-zＡ-Ｚａ-ｚ]{2,}|[「『]/;
const GENERIC_RE = /にとって(?:も)?(?:重要|大切)|と(?:い|言)えます|可能性があります|きっかけにできます|見直す(?:良い)?機会|参考になります|すべての人に関係/;

const bigrams = (s: string) => {
  const t = s.replace(/[\s、。「」（）()・]/g, "");
  const out = new Set<string>();
  for (let i = 0; i < t.length - 1; i++) out.add(t.slice(i, i + 2));
  return out;
};

function similar(a: string, b: string): number {
  const x = bigrams(a);
  const y = bigrams(b);
  if (!x.size || !y.size) return 0;
  let both = 0;
  for (const g of x) if (y.has(g)) both += 1;
  return both / Math.min(x.size, y.size);
}

/**
 * 記事ごとの中身がある点だけを残す（一般論・要点のくり返しは外す）。影響が小さい記事・残らない記事は欄ごと出さない（null）
 */
export function concreteWorkMoney(workMoney: WorkMoney | null, repeatOf: string[] = []): WorkMoney | null {
  if (!workMoney || workMoney.relevance === "low") return null;
  const points = workMoney.points.filter(
    (p) => FACT_RE.test(p) && !GENERIC_RE.test(p) && !repeatOf.some((r) => r && similar(p, r) >= 0.7),
  );
  return points.length ? { ...workMoney, points } : null;
}

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
