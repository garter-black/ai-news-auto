/**
 * AIサービス図鑑（/products）の「中身があるか」の判定と、表示する文の掃除（ほかの lib を読まない純粋な関数だけ。
 * Next.js を起動せずに tests/test_products_hub.cjs で検査する）。
 *
 * 2026-11-03 オーナーの指摘「内容がすごく薄い」（台帳 W13）:
 * - 一覧に出して検索に載せるのは、確かめた公式の料金（content/pricing）がつながり、日付つきの動き（記事）と
 *   中身のある説明がそろった製品だけ。足りない製品は一覧に出さず noindex（データがそろえば自動で載る）
 * - 「記載なし」「元記事には〜」「未定」のような答えになっていない文は、文ごと出さない
 * - 小見出しは決まった型の問いかけ（「何ができる？」「料金は？」）にしない。落ち着いた名詞の見出しにする
 */

/** 答えになっていない文（文ごと消す） */
const NON_ANSWER_RE =
  /元記事|記載(?:は|が)?(?:なし|ありません|ない|されていません)|言及(?:は|が)?(?:なし|ありません|されていません)|判断でき(?:ません|ない)|未発表|未定|不明|明らかにされていません|発表されていません|わかっていません|分かっていません/;

/** 文に分ける（。！？で区切る。区切りの無い文もそのまま1文） */
function sentences(text: string): string[] {
  return text.match(/[^。！？!?]+[。！？!?]?/g)?.map((s) => s.trim()).filter(Boolean) ?? [];
}

/** 答えになっていない文を消した残り（全部消えたら空文字） */
export function cleanProductText(text: string | null | undefined): string {
  const t = (text ?? "").replace(/[（(][^（）()]*(?:元記事|記載なし|言及なし)[^（）()]*[）)]/g, "").trim();
  if (!t) return "";
  return sentences(t)
    .filter((s) => !NON_ANSWER_RE.test(s))
    .join("")
    .trim();
}

/** 箇条書き: 掃除して空になった項目は消す */
export function cleanProductList<T extends { text: string }>(items: readonly T[] | null | undefined): T[] {
  return (items ?? []).map((i) => ({ ...i, text: cleanProductText(i.text) })).filter((i) => i.text.length > 0);
}

/**
 * よくある質問の問いを、落ち着いた「です・ます」の問いにする（「使える？」→「使えますか？」）。
 * 「〜は？」で終わる短い問い（「利用枠の確認方法は？」）はそのまま。
 */
export function politeQuestion(q: string): string {
  const t = q.trim().replace(/\?$/, "？");
  if (!t.endsWith("？")) return t;
  const rules: [RegExp, string][] = [
    [/なる？$/, "なりますか？"],
    [/ある？$/, "ありますか？"],
    [/かかる？$/, "かかりますか？"],
    [/変わる？$/, "変わりますか？"],
    [/違う？$/, "違いますか？"],
    [/([いきぎしじちにひびみりえけげせぜてでねへべめれ])る？$/, "$1ますか？"],
    [/([使買払言])う？$/, "$1いますか？"],
    [/できない？$/, "できませんか？"],
    [/(いい|よい)？$/, "よいですか？"],
    [/(何|いつ|どこ|誰|だれ|いくら|どれ)？$/, "$1ですか？"],
  ];
  for (const [re, to] of rules) if (re.test(t)) return t.replace(re, to);
  return t;
}

/** 表の注記（「〜がある。」「〜ている。」の常体）を、ページの本文に合わせて「です・ます」にする */
export function politeNote(text: string): string {
  return text
    .replace(/されていない。/g, "されていません。")
    .replace(/ていない。/g, "ていません。")
    .replace(/ている。/g, "ています。")
    .replace(/がある。/g, "があります。")
    .replace(/はない。/g, "はありません。")
    .replace(/である。/g, "です。")
    .replace(/建て(（[^）]*）)?。/g, "建て$1です。");
}

/** 決まった型の問いかけの見出し（製品ページの小見出しに使わない。見張り scripts/owner_rules_audit.py の W13 と同じ考え方） */
export const TEMPLATE_PRODUCT_HEADINGS = ["何ができる？", "料金は？", "日本で使える？", "どうやって使う？", "これまでと何が違う？", "前のモデルと何が違う？"];

/** 公式データとのつなぎ（content/product_sources.json の1件） */
export type ProductSource = {
  pricing?: { product: string; plans?: string[]; models?: string[] };
  why?: string;
  japan?: string;
  audience?: string;
  /** 図鑑のカードとページの冒頭の説明（leadSources の記事に書かれている事実だけ） */
  lead?: string;
  leadSources?: string[];
  /** 「同じ価格帯のサービスとの比較」に使う段（全プランを載せる製品は standard） */
  rivalsTier?: string;
  /** 発表の履歴から外す記事（インタビューなど、記事の日付と出来事の日付がずれるもの） */
  timelineSkip?: string[];
  /** 報道向けの公式プレスキット・ブランド素材の画像（KV の /img/brand-<名前>）。縮小と余白のほかは変えない */
  image?: { src: string; credit: string; sourceUrl: string; checkedAt?: string; note?: string };
};

/** 料金の JSON（content/pricing/<id>.json）のうち、判定に使う部分 */
type PricingJsonLike = { plans?: { id: string; prices?: unknown[] }[]; models?: { id: string; status?: string }[] };

/** つなぎが指す公式の料金の行の数（行が無い・料金ページに載っていないモデルは数えない） */
export function officialPriceRowCount(source: ProductSource | undefined, pricing: Record<string, PricingJsonLike | undefined>): number {
  const link = source?.pricing;
  if (!link) return 0;
  const data = pricing[link.product];
  if (!data) return 0;
  const plans = (link.plans ?? []).filter((id) => data.plans?.some((p) => p.id === id && (p.prices?.length ?? 0) > 0));
  const models = (link.models ?? []).filter((id) => data.models?.some((m) => m.id === id && m.status !== "not_listed"));
  return plans.length + models.length;
}

export type ListingInput = {
  /** 概要（掃除のあと） */
  overview: string;
  /** 公式の料金の行の数 */
  officialPriceRows: number;
  /** 日付つきの動き（公開中の記事につながるもの） */
  datedEvents: number;
  /** 公開中の関連記事の数 */
  liveArticles: number;
  /** 機能・仕様の項目（掃除のあと） */
  features: number;
};

export type Listing = { listed: boolean; reasons: string[] };

/** 一覧に出して検索に載せてよいか（足りないものを理由として返す） */
export function productListing(input: ListingInput): Listing {
  const reasons: string[] = [];
  if (input.overview.length < 40) reasons.push("概要が短い");
  if (input.officialPriceRows < 1) reasons.push("確かめた公式の料金がつながっていない（content/product_sources.json）");
  if (input.datedEvents < 2 && input.liveArticles < 3) reasons.push("日付つきの動き・関連記事が少ない");
  if (input.features < 2) reasons.push("機能・仕様の項目が少ない");
  return { listed: reasons.length === 0, reasons };
}

/** 関連度（一覧の並び）: 直近60日の関連記事を重く、全体の記事数を軽く */
export function productRelevance(articleDates: string[], now: Date): number {
  const recent = articleDates.filter((d) => now.getTime() - new Date(d).getTime() < 60 * 86400_000).length;
  return recent * 3 + articleDates.length;
}
