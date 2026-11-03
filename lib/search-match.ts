/**
 * サイト内検索の照合（ブラウザで動く。ライブラリは使わない）。
 * 日本語は単語の区切りが無いので、正規化した文字列の「部分一致」で探す。
 * - 全角英数・半角カナを NFKC でそろえ、英字は小文字にする（「ＧＰＴ」＝「gpt」、「ｸﾛｰﾄﾞ」＝「クロード」）。
 *   カタカナはひらがなに寄せない（「メタ」が「始めた」に当たるなど、短い言葉の外れが増えるため）
 * - 空白・かっこ・中黒などの記号は消す（「Claude Code」＝「ClaudeCode」、「『Gemini』」＝「Gemini」）
 * - 空白で区切った言葉はすべて含む記事だけ（AND）
 * - 会社・製品の名前はカタカナと英字のどちらでも当たる（「クロード」→Claude、「ジェミニ」→Gemini）。
 *   言葉の一部に名前が入っていても言い換える（「クロードコード」→「claudeコード」「claudecode」）
 *
 * 並び順（台帳 W24）: 当たりの強さ（題名＞会社・製品のタグ＞要約・ジャンル名）→ 新しい順。
 * 取り下げた記事・検索に出さない記事（noindex）は索引に入れない（lib/search-index.mjs）。
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
 * 呼び方の言い換え（どちらで検索しても当たるように）。左が見出し上の表記（scripts/headline_normalize.py の会社名の表と合わせる）。
 * 短すぎて別の言葉に当たりやすいカタカナ（「メタ」「ソラ」「ラマ」「マック」など）は入れない
 */
const ALIASES: [string, string[]][] = [
  // 会社
  ["Microsoft", ["マイクロソフト"]],
  ["Google", ["グーグル"]],
  ["OpenAI", ["オープンAI", "オープンエーアイ"]],
  ["Apple", ["アップル"]],
  ["Amazon", ["アマゾン"]],
  ["Meta", ["メタ社"]],
  ["NVIDIA", ["エヌビディア", "エヌヴィディア"]],
  ["Anthropic", ["アンソロピック", "アンスロピック", "アンスロピーク"]],
  ["DeepSeek", ["ディープシーク"]],
  ["DeepMind", ["ディープマインド"]],
  ["xAI", ["エックスエーアイ"]],
  ["Mistral", ["ミストラル"]],
  ["Perplexity", ["パープレキシティ", "パープレ"]],
  ["Samsung", ["サムスン"]],
  ["Salesforce", ["セールスフォース"]],
  ["Atlassian", ["アトラシアン"]],
  ["Adobe", ["アドビ"]],
  ["Intel", ["インテル"]],
  ["Qualcomm", ["クアルコム"]],
  ["Tesla", ["テスラ"]],
  ["Netflix", ["ネットフリックス"]],
  ["YouTube", ["ユーチューブ"]],
  ["Hugging Face", ["ハギングフェイス"]],
  ["SoftBank", ["ソフトバンク"]],
  ["Oracle", ["オラクル"]],
  ["IBM", ["アイビーエム"]],
  ["Canva", ["キャンバ"]],
  ["Notion", ["ノーション"]],
  ["Spotify", ["スポティファイ"]],
  // 製品・サービス
  ["Claude", ["クロード"]],
  ["Claude Code", ["クロードコード"]],
  ["Gemini CLI", ["ジェミニCLI"]],
  ["Gemini", ["ジェミニ", "ジェミナイ"]],
  ["ChatGPT", ["チャットGPT", "チャットジーピーティー"]],
  ["GPT", ["ジーピーティー"]],
  ["Copilot", ["コパイロット"]],
  ["Grok", ["グロック"]],
  ["Codex", ["コーデックス"]],
  ["Dify", ["ディファイ", "ディフィ"]],
  ["Midjourney", ["ミッドジャーニー"]],
  ["Stable Diffusion", ["ステーブルディフュージョン"]],
  ["iPhone", ["アイフォン", "アイフォーン"]],
  ["iPad", ["アイパッド"]],
  ["MacBook", ["マックブック"]],
  ["Android", ["アンドロイド"]],
  ["Windows", ["ウィンドウズ", "ウインドウズ"]],
  ["Galaxy", ["ギャラクシー"]],
  ["PlayStation", ["プレイステーション", "プレステ"]],
  ["Nintendo Switch", ["ニンテンドースイッチ"]],
  ["Excel", ["エクセル"]],
  ["Slack", ["スラック"]],
  ["Kindle", ["キンドル"]],
  ["NISA", ["ニーサ"]],
  ["iDeCo", ["イデコ"]],
];

/** 正規化した呼び方 → 同じものの呼び方（正規化済み）の一覧 */
const ALIAS_MAP = new Map<string, string[]>();
for (const [latin, others] of ALIASES) {
  const group = [latin, ...others].map(normalizeForSearch);
  for (const key of group) ALIAS_MAP.set(key, group);
}
/** 言葉の一部に入った呼び方を探す順（長い呼び方が先。「チャットGPT」を「GPT」より先に） */
const ALIAS_KEYS = [...ALIAS_MAP.keys()].filter((k) => k.length >= 3).sort((a, b) => b.length - a.length);

/** 1つの言葉の言い換え候補（そのまま・言葉全体の言い換え・言葉の一部の言い換え）。多すぎないように8つまで */
export function termVariants(term: string): string[] {
  const out = new Set<string>([term]);
  for (const alt of ALIAS_MAP.get(term) ?? []) out.add(alt);
  for (const key of ALIAS_KEYS) {
    if (key === term || !term.includes(key)) continue;
    for (const alt of ALIAS_MAP.get(key) ?? []) if (alt !== key) out.add(term.replace(key, alt));
    break; // いちばん長い呼び方だけ言い換える
  }
  return [...out].slice(0, 8);
}

/** 検索の言葉を、言い換えの候補つきで返す（[[候補…], [候補…]] のどれかを各言葉で含めば当たり） */
export function queryTerms(query: string): string[][] {
  return query
    .normalize("NFKC")
    .split(/[\s　]+/)
    .map(normalizeForSearch)
    .filter(Boolean)
    .slice(0, 6)
    .map(termVariants);
}

/** タグ（空白区切り）を正規化した一覧にする（会社・製品の名前と完全に同じかを見る） */
export function normalizeTags(tags: string): string[] {
  return tags.split(/\s+/).map(normalizeForSearch).filter(Boolean);
}

/**
 * 当たりの強さ（0 は外れ）。言葉ごとに一番強い当たりを足す。
 * - 題名に入っている: 3（題名の頭＝主語なら +1、先頭12字以内なら +0.5）
 * - 会社・製品のタグと同じ（「Claude」で検索してタグが「Claude」）: 2
 * - 要約・タグの一部・ジャンル名だけ: 1
 * 同じ強さなら新しい順（並べる側。app/search/search-view.tsx）
 */
export function scoreRow(terms: string[][], title: string, rest: string, tags: string[] = []): number {
  let score = 0;
  for (const options of terms) {
    let best = 0;
    for (const term of options) {
      const at = title.indexOf(term);
      if (at >= 0) best = Math.max(best, 3 + (at === 0 ? 1 : at < 12 ? 0.5 : 0));
      else if (tags.includes(term)) best = Math.max(best, 2);
      else if (rest.includes(term)) best = Math.max(best, 1);
    }
    if (best === 0) return 0;
    score += best;
  }
  return score;
}

/** 当たった記事を並べる: 強さ → 新しい順（order が小さいほど新しい） */
export function rankHits<T extends { order: number }>(hits: { item: T; score: number }[]): T[] {
  return hits.sort((a, b) => b.score - a.score || a.item.order - b.item.order).map((h) => h.item);
}
