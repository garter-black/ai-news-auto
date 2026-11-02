import type { ArticleSummary } from "@/lib/articles";

/**
 * 「使えるかどうか」の判定。記事生成時に Gemini が本文の料金・提供状況から付ける。
 * サイソクの軸は「速さ」だけでなく「今それを使えるかを最速で判定する」こと。
 */
export type Verdict = {
  status: "available" | "limited" | "soon" | "demo";
  japan: "ok" | "partial" | "no" | "unknown";
  price: string;
  free: boolean | null;
  audience: string;
  take: string;
  /** 判定した日時。無ければ記事の生成日時 */
  judgedAt?: string;
  /** 判定カードの種類（scripts/web_articles.py の genre_verdict）。無ければ記事のジャンルから決める（verdictKind） */
  kind?: VerdictKind;
  /** スポーツ・制度: 日時・開始日・期限 */
  when?: string;
  /** スポーツ: テレビ放送・ネット配信 */
  broadcast?: string;
  /** 日本での提供を、当サイトの確認表（content/japan_availability.json）で直したときの出典 */
  japanSource?: { name: string; url: string; checkedAt: string };
};

/** 判定カードの種類。製品（使える？）／スポーツ（観戦できる？）／制度（申し込める？） */
export type VerdictKind = "product" | "sports" | "public";

/** スポーツ・制度の記事には製品の言い方（「日本でも提供予定」など）を出さない */
export function verdictKind(article: { genreSlug?: string; verdict?: Verdict | null }): VerdictKind {
  const kind = article.verdict?.kind;
  if (kind === "sports" || kind === "public" || kind === "product") return kind;
  if (article.genreSlug === "sports") return "sports";
  if (article.genreSlug === "public") return "public";
  return "product";
}

export const STATUS_LABEL_BY_KIND: Record<VerdictKind, Record<Verdict["status"], string>> = {
  product: {
    available: "今すぐ使える",
    limited: "一部で提供中",
    soon: "提供はこれから",
    demo: "研究・デモ段階",
  },
  sports: {
    available: "観戦・参加できる",
    limited: "抽選・一部で受付",
    soon: "受付・開催はこれから",
    demo: "受付・開催はこれから",
  },
  public: {
    available: "受付中・実施中",
    limited: "対象が限られる",
    soon: "これから始まる",
    demo: "案・検討中",
  },
};

export const VERDICT_HEADING: Record<VerdictKind, string> = {
  product: "サイソクの判断",
  sports: "観戦・参加の判断",
  public: "申し込みの判断",
};

/** 判定カードの4つの欄（種類ごとに問いを変える） */
export function verdictFacts(verdict: Verdict, kind: VerdictKind): { label: string; value: string; muted?: boolean }[] {
  const unknown = (value?: string) => !value || /^(不明|記載なし|言及なし|元記事では言及なし)$/.test(value.trim());
  if (kind === "sports") {
    return [
      { label: "観戦・参加できる？", value: STATUS_LABEL_BY_KIND.sports[verdict.status], muted: verdict.status !== "available" },
      { label: "いつ？", value: verdict.when || "記載なし", muted: unknown(verdict.when) },
      { label: "放送・配信は？", value: verdict.broadcast || "記載なし", muted: unknown(verdict.broadcast) },
      { label: "チケット・参加費は？", value: verdict.price || "記載なし", muted: unknown(verdict.price) },
    ];
  }
  if (kind === "public") {
    return [
      { label: "申し込める？", value: STATUS_LABEL_BY_KIND.public[verdict.status], muted: verdict.status !== "available" },
      { label: "いつから・期限は？", value: verdict.when || "記載なし", muted: unknown(verdict.when) },
      { label: "費用・金額は？", value: verdict.price || "記載なし", muted: unknown(verdict.price) },
      { label: "対象は？", value: verdict.audience || "記載なし", muted: unknown(verdict.audience) },
    ];
  }
  return [
    { label: "日本から使える？", value: japanLabel(verdict), muted: verdict.japan === "unknown" || verdict.status === "soon" || verdict.status === "demo" },
    { label: "料金は？", value: verdict.price || "不明", muted: unknown(verdict.price) },
    { label: "無料で試せる？", value: freeLabel(verdict.free), muted: verdict.free === null },
    { label: "誰に向いている？", value: verdict.audience || "—", muted: !verdict.audience },
  ];
}

/** 一覧で出す3段階。迷わないよう「使える／一部／まだ」の3つだけにする */
export type Usability = "go" | "partial" | "wait";

export function usability(verdict: Verdict): Usability {
  if (verdict.status === "available" && verdict.japan !== "no") return "go";
  if (verdict.status === "available" || verdict.status === "limited") return "partial";
  return "wait";
}

export const USABILITY_LABEL: Record<Usability, string> = {
  go: "使える",
  partial: "一部",
  wait: "まだ",
};

export const STATUS_LABEL: Record<Verdict["status"], string> = {
  available: "今すぐ使える",
  limited: "一部で提供中",
  soon: "提供はこれから",
  demo: "研究・デモ段階",
};

export const JAPAN_LABEL: Record<Verdict["japan"], string> = {
  ok: "利用できる",
  partial: "条件付きで利用可",
  no: "日本では未提供",
  unknown: "記載なし",
};

/** 提供前の製品で「日本では未提供」と出すと日本だけ除外されたように読めるので「提供前」にする */
export function japanLabel(verdict: Verdict): string {
  if (verdict.status === "soon" || verdict.status === "demo") {
    return verdict.japan === "ok" ? "日本でも提供予定" : "提供前";
  }
  return JAPAN_LABEL[verdict.japan];
}

export function freeLabel(free: boolean | null): string {
  if (free === true) return "無料で試せる";
  if (free === false) return "有料のみ";
  return "記載なし";
}

/** まとめ記事や判定のない古い記事には出さない */
export function verdictOf(article: ArticleSummary): Verdict | undefined {
  if (article.type === "roundup") return undefined;
  return article.verdict ?? undefined;
}
