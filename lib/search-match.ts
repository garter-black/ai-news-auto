/**
 * サイト内検索の照合（ブラウザで動く。ライブラリは使わない）。
 * 日本語は単語の区切りが無いので、正規化した文字列の「部分一致」で探す。
 * - 全角英数・半角カナを NFKC でそろえ、英字は小文字にする（「ＧＰＴ」＝「gpt」）。
 *   カタカナはひらがなに寄せない（「メタ」が「始めた」に当たるなど、短い言葉の外れが増えるため）
 * - 空白・かっこ・中黒などの記号は消す（「Claude Code」＝「ClaudeCode」、「『Gemini』」＝「Gemini」）
 * - 空白で区切った言葉はすべて含む記事だけ（AND）。会社名はカタカナと英字のどちらでも当たる（見出しは英字にそろえている）
 */

/** [slug, 題名, 要約, タグ, ジャンルの slug, 日付, サムネイル]（lib/search-index.mjs） */
export type SearchRow = [string, string, string, string, string, string, string];

const SYMBOLS = /[\s　「」『』【】〔〕［］()（）〈〉《》・･、。，．,.!！?？:：;；"'“”‘’`~〜\-‐－—―_/／|｜]/g;

export function normalizeForSearch(text: string): string {
  return text
    .normalize("NFKC")
    .toLowerCase()
    .replace(SYMBOLS, "");
}

/**
 * 呼び方の言い換え（どちらで検索しても当たるように）。左が正規化前の見出し上の表記（scripts/headline_normalize.py の会社名の表と合わせる）
 */
const ALIASES: [string, string[]][] = [
  ["Microsoft", ["マイクロソフト"]],
  ["Google", ["グーグル"]],
  ["OpenAI", ["オープンAI", "オープンエーアイ"]],
  ["Apple", ["アップル"]],
  ["Amazon", ["アマゾン"]],
  ["Meta", ["メタ社"]],
  ["NVIDIA", ["エヌビディア"]],
  ["Anthropic", ["アンソロピック", "アンスロピック"]],
  ["ChatGPT", ["チャットGPT", "チャットジーピーティー"]],
  ["iPhone", ["アイフォン", "アイフォーン"]],
];

const ALIAS_MAP = new Map<string, string[]>();
for (const [latin, others] of ALIASES) {
  const group = [latin, ...others].map(normalizeForSearch);
  for (const key of group) ALIAS_MAP.set(key, group);
}

/** 検索の言葉を、言い換えの候補つきで返す（[[候補…], [候補…]] のどれかを各言葉で含めば当たり） */
export function queryTerms(query: string): string[][] {
  return query
    .normalize("NFKC")
    .split(/[\s　]+/)
    .map(normalizeForSearch)
    .filter(Boolean)
    .slice(0, 6)
    .map((term) => ALIAS_MAP.get(term) ?? [term]);
}

/** 当たりの強さ（0 は外れ）。題名に入っていれば 3、要約・タグ・ジャンル名だけなら 1。題名の先頭に近いほど少し上 */
export function scoreRow(terms: string[][], title: string, rest: string): number {
  let score = 0;
  for (const options of terms) {
    let best = 0;
    for (const term of options) {
      const at = title.indexOf(term);
      if (at >= 0) best = Math.max(best, 3 + (at < 12 ? 0.5 : 0));
      else if (rest.includes(term)) best = Math.max(best, 1);
    }
    if (best === 0) return 0;
    score += best;
  }
  return score;
}
