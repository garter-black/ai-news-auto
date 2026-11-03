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
  product: "サイソクの判定",
  sports: "観戦・参加の可否",
  public: "申し込みの可否",
};

/**
 * 「記載なし」「不明」など、答えになっていない値か。
 * 判定カード・一覧の1行に「記載なし」が3つも並ぶと読者には壊れて見えるので、出さずに欄ごと省く（2026-11-03 の画面の点検）。
 * 「参加費の記載なし」「料金は元記事では言及なし」のような言い回しも含める。
 */
export function isUnknownValue(value?: string | null): boolean {
  const v = (value ?? "").trim();
  if (!v || /^[—―\-–ー・?？]+$/.test(v)) return true;
  return /^(不明|未定|記載なし|言及なし|情報なし|元記事では言及なし|未確認|日本は未確認|未発表|法人向け)$/.test(v) || /(の)?(記載|言及|情報)(が)?(なし|ない|ありません)$/.test(v) || /^.{0,12}は不明$/.test(v);
}

/** 判定カードの4つの欄（種類ごとに問いを変える） */
export function verdictFacts(verdict: Verdict, kind: VerdictKind): { label: string; value: string; muted?: boolean }[] {
  const unknown = (value?: string) => isUnknownValue(value);
  if (kind === "sports") {
    return [
      { label: "観戦・参加", value: STATUS_LABEL_BY_KIND.sports[verdict.status], muted: verdict.status !== "available" },
      { label: "日程", value: verdict.when || "記載なし", muted: unknown(verdict.when) },
      { label: "放送・配信", value: verdict.broadcast || "記載なし", muted: unknown(verdict.broadcast) },
      { label: "チケット・参加費", value: verdict.price || "記載なし", muted: unknown(verdict.price) },
    ];
  }
  if (kind === "public") {
    return [
      { label: "受付状況", value: STATUS_LABEL_BY_KIND.public[verdict.status], muted: verdict.status !== "available" },
      { label: "受付期間", value: verdict.when || "記載なし", muted: unknown(verdict.when) },
      { label: "費用・金額", value: verdict.price || "記載なし", muted: unknown(verdict.price) },
      { label: "対象", value: verdict.audience || "記載なし", muted: unknown(verdict.audience) },
    ];
  }
  return [
    // 欄の名前は質問の形にしない（仕様表のような名詞。2026-11-03 オーナー指摘「定型の質問が機械的」）
    { label: "日本での提供", value: japanLabel(verdict), muted: verdict.japan === "unknown" || verdict.status === "soon" || verdict.status === "demo" },
    { label: "料金", value: verdict.price || "不明", muted: unknown(verdict.price) },
    // 無料枠は「ある」ときだけ出す（車の外装オプションに「有料のみ」と出ていた。2026-11-03）
    { label: "無料枠", value: verdict.free === true ? freeLabel(true) : "記載なし", muted: verdict.free !== true },
    { label: "主な対象", value: verdict.audience || "—", muted: !verdict.audience },
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
  go: "提供中",
  partial: "一部提供",
  wait: "提供前",
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

/**
 * 「日本から使える？」の欄。
 * 提供前の製品で「日本では未提供」と出すと日本だけ除外されたように読めるので「提供前」にする。
 * ただし日本での扱いが分からない（unknown）ときは「提供前」と言い切らない（本文が「順次提供されています」と書いていることがある。
 * 2026-11-03 の点検で見つかった食い違い。生成時にも scripts/quality.py の align_verdict で本文にそろえる）
 */
export function japanLabel(verdict: Verdict): string {
  if (verdict.status === "soon") {
    if (verdict.japan === "ok") return "日本でも提供予定";
    if (verdict.japan === "partial") return "条件付きで提供予定";
    if (verdict.japan === "unknown") return "日本は未確認";
    return "提供前";
  }
  if (verdict.status === "demo") {
    return verdict.japan === "ok" ? "日本でも提供予定" : verdict.japan === "unknown" ? "日本は未確認" : "提供前";
  }
  return JAPAN_LABEL[verdict.japan];
}

export function freeLabel(free: boolean | null): string {
  if (free === true) return "無料で試せる";
  if (free === false) return "有料のみ";
  return "記載なし";
}

/**
 * 製品・サービスの発表ではないニュース（事故・情報流出・攻撃、買収・上場・人事などの会社の動き、表彰、統計・業績）。
 * 「使える／提供前」の判定は製品・サービスが使えるかの答えなので、これらには出さない（2026-11-03 の画面の点検 A。
 * 北陸電力のメールアドレス流出に「使える・無料」と出ていた）。
 * 記事を作る側（scripts/web_articles.py の NON_PRODUCT_RE）と同じ文字列。変えたら両方を直す（tests/test_verdict_scope.py が照らし合わせる）
 */
export const NON_PRODUCT_PATTERN =
  "流出(?!を防|対策)|(?:漏えい|漏洩)(?:し|が|の(?:可能性|恐れ|疑い))|不正アクセス(?!を防|対策)|不正利用(?!を防|対策)|サイバー攻撃(?:を受け|で|被害)|ランサムウェア被害|悪用を確認|悪用され|" +
  "障害が発生|大規模障害|で通信障害|通信障害が|に脆弱性|脆弱性が見つか|欠陥を発見|欠陥が見つか|インシデント|事件|逮捕|書類送検|訴訟|提訴|行政処分|業務改善命令|リコール|" +
  "買収|合併|経営統合|持株会社|分社|上場申請|新規上場|株式上場|資金調達|出資|子会社化|就任|退任|社長交代|人事異動|企業再編|連携協定|協定を締結|業務提携|資本提携|" +
  "表彰|受賞|選出|アワード|Awards?|" +
  "統計(?!的)|倒産|件数|調査結果|決算|業績|自社株買い|株主還元|配当|物価|雇用統計|失業率|景気|口座数|契約数|販売台数|出荷台数|上方修正|下方修正|前年同月比|前年比|突破|有効求人倍率|短観";
const NON_PRODUCT_RE = new RegExp(NON_PRODUCT_PATTERN);
const NOT_PRODUCT_TAKE_RE = /製品(?:の)?発表ではな|新製品ではな|サービスの発表ではな|発表はな(?:い|く)/;

/** 製品・サービスが使えるかを答える記事か（見出しとタグで見る） */
export function isProductNews(article: { title: string; tags?: string[] }): boolean {
  return !NON_PRODUCT_RE.test(`${article.title} ${(article.tags ?? []).join(" ")}`);
}

/** まとめ記事・判定のない古い記事・製品の発表でないニュースには出さない */
export function verdictOf(article: Pick<ArticleSummary, "type" | "verdict" | "genreSlug" | "title" | "tags">): Verdict | undefined {
  if (article.type === "roundup" || !article.verdict) return undefined;
  // 判定のひとこと自体が「製品の発表ではない」と言っている記事には出さない（研究・取材・告知など。2026-11-03）
  if (NOT_PRODUCT_TAKE_RE.test(article.verdict.take ?? "")) return undefined;
  // 制度の記事でも、統計・事件・表彰などは「申し込める？」の答えにならない。スポーツ（「代表に選出」など）は観戦の判定なので残す
  if (verdictKind(article) !== "sports" && !isProductNews(article)) return undefined;
  return article.verdict;
}

/**
 * 一覧の札（使える／一部／まだ）。製品・サービスの記事だけ。
 * スポーツ・制度は判定カード（観戦できる？・申し込める？）を記事ページに出すが、一覧の札は付けない（製品の言い方になるため）
 */
export function listUsability(article: Pick<ArticleSummary, "type" | "verdict" | "genreSlug" | "title" | "tags">): Usability | undefined {
  const verdict = verdictOf(article);
  if (!verdict || verdictKind(article) !== "product") return undefined;
  return usability(verdict);
}
