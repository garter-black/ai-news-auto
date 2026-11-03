import fs from "@/lib/content-fs";
import path from "node:path";
import type { ArticleSummary } from "@/lib/articles";
import { CHOOSE_GUIDES } from "@/lib/choose-data";
import { MIN_SCORE as OFFER_MIN_SCORE, isB2BArticle, isRoundupArticle, matchesHostingTopic, relevanceScore } from "@/lib/offers";
import { isDisasterArticle } from "@/lib/related";

/**
 * 記事に合う広告（lib/offers.ts）が無いときに出す「あわせて読みたい」。
 * 関係の薄い広告で枠を埋める代わりに、自前の選び方ガイド（/choose）・比較（/compare）・使い方（/guides）へ案内する。
 * 読者（20〜50代の働く男性：仕事・お金・副業・独立・AIツール）に合わせて、話題ごとの行き先を決めている。
 * 題名と説明は各ページのデータから読むので、ページが無くなれば自動で候補から外れる。
 */

export type ReadNextLink = {
  href: string;
  kind: "choose" | "compare" | "guide" | "category";
  label: string;
  note?: string;
};

/** consumer: 個人向けのサーバー・副業の案内。法人・企業向けの記事には、話題がサーバーそのもののときしか出さない */
/** side: まとめ記事（lib/offers.ts の isRoundupArticle）では案内しない脇の話題（「詐欺対策も」の1語でセキュリティソフトの比較など。2026-11-03 の点検 D） */
type Candidate = { href: string; match: string; genres?: string[]; consumer?: boolean; side?: boolean };

/** 上ほど具体的。同点なら上を選ぶ */
const CANDIDATES: Candidate[] = [
  // AIツール
  { href: "/guides/claude-code-how-to-use", match: "Claude Code|Codex|コーディング|AI駆動開発|バイブコーディング|Copilot|Cursor|開発ツール" },
  { href: "/guides/claude-pricing", match: "Claude|Anthropic" },
  { href: "/guides/chatgpt-pricing", match: "ChatGPT|OpenAI|GPT-?\\d" },
  { href: "/guides/notebooklm-how-to-use", match: "NotebookLM" },
  { href: "/guides/gemini-free-plan", match: "Gemini|Google AI|グーグルのAI" },
  { href: "/guides/chatgpt-smartphone", match: "ChatGPT(?:の|を)?(?:スマホ|アプリ|iPhone|Android)|(?:スマホ|iPhone|Android)(?:版|で|の)ChatGPT" },
  { href: "/choose/dify-hosting", match: "\\bDify\\b|ディファイ|n8n" },
  { href: "/compare/vps-dify-n8n", match: "AIエージェント|(?<!サイバー)エージェント|ワークフロー|セルフホスト|VPS", consumer: true },
  { href: "/compare/image-generation", match: "画像生成|イラスト|Stable Diffusion|Midjourney|デザインツール|Figma" },
  { href: "/compare/video-editing", match: "動画生成|動画編集|YouTube|ショート動画|配信者" },
  { href: "/compare/ai-voice-recorder", match: "議事録|文字起こし|録音|ボイスレコーダー|音声認識" },
  { href: "/compare/ai-writing-tools", match: "文章|ライティング|記事作成|執筆|校正" },
  { href: "/compare/ai-english", match: "英語|英会話|翻訳|語学|留学" },
  { href: "/compare/local-llm-pc", match: "ローカルLLM|ローカルAI|GPU|RTX|GeForce|VRAM|NVIDIA|半導体|ゲーミングPC", genres: ["ai", "tech-gadget"] },
  { href: "/compare/ai-mini-pc", match: "ミニPC|NPU|Copilot\\+ ?PC" },
  { href: "/compare/security-software", match: "詐欺|フィッシング|不正アクセス|情報漏え?い|情報流出|個人情報|サイバー(?!エージェント)|ランサム|脆弱性|ウイルス|マルウェア|なりすまし|セキュリティ|攻撃者|サイバー攻撃|侵入|ハッキング", side: true },
  { href: "/compare/kids-programming", match: "子ども向け|小学生|中学生|プログラミング教育|プログラミング教室|STEAM" },
  // 働き方・お金
  { href: "/choose/internet-for-ai", match: "光回線|ネット回線|WiMAX|Wi-?Fi|格安SIM|スマホ料金|携帯料金|通信料金|5G|テレワーク|リモートワーク|在宅勤務", side: true },
  { href: "/compare/rental-server", match: "副業|複業|フリーランス|個人事業|独立|起業|開業|創業|ホームページ|Webサイト|ブログ|WordPress|集客|ネットショップ|ECサイト", consumer: true, side: true },
  { href: "/choose/ai-career", match: "転職|求人|採用|就職|雇用|就業者|人材|リスキリング|学び直し|スキル|資格|試験|キャリア|賃金|給与|年収|働き方|人手不足|AI人材" },
];

/**
 * どのガイドも当たらない、お金・税金・ビジネスの記事だけ、そのジャンルの最新ニュース一覧（/genres/<slug>）へ案内する。
 * それ以外（スポーツ・芸能など）は何も出さない。関係のない既定のガイドは出さない。
 */
const MONEY_GENRES = new Set(["business", "investment", "real-estate"]);
const MONEY_TITLE =
  /お金|マネー|家計|節約|資産|投資|NISA|iDeCo|証券|年金|老後|税|確定申告|インボイス|金利|ローン|物価|値上げ|円安|円高|給付|補助金|助成金|支援金|賃金|給与|給料|ボーナス|手取り|副業|転職|就職|働き方|中小企業|個人事業|起業/;

/** ガイドを出す基準点。広告枠（lib/offers.ts の MIN_SCORE）と同じ。見出しに当たるか、タグと要約の両方で裏付けが要る */
const MIN_SCORE = OFFER_MIN_SCORE;

function readJson<T>(dir: string, slug: string): T | null {
  const full = path.join(/*turbopackIgnore: true*/ process.cwd(), "content", dir, `${slug}.json`);
  if (!fs.existsSync(full)) return null;
  try {
    return JSON.parse(fs.readFileSync(full, "utf8")) as T;
  } catch {
    return null;
  }
}

type PageData = { title?: string; shortName?: string; description?: string };

/** 行き先の題名と説明（ページが無ければ undefined） */
export function resolveReadNext(href: string): ReadNextLink | undefined {
  const [, section, slug] = href.split("/");
  if (!slug) return undefined;
  if (section === "choose") {
    const guide = CHOOSE_GUIDES.find((g) => g.slug === slug);
    return guide && { href, kind: "choose", label: guide.articleLabel, note: guide.articleNote };
  }
  if (section === "compare") {
    const data = readJson<PageData>("buying-guides", slug);
    // 【2026年版】などの付記は外して短くする
    return data?.title ? { href, kind: "compare", label: data.title.replace(/【[^】]*】/g, " ").replace(/\s+/g, " ").trim(), note: data.description } : undefined;
  }
  if (section === "guides") {
    const data = readJson<PageData>("howto", slug);
    return data?.title ? { href, kind: "guide", label: data.title, note: data.description } : undefined;
  }
  return undefined;
}

/**
 * 記事に合うガイドを関連の高い順に返す（基準点以上だけ）。exclude は同じページに既に出ているリンク（重複させない）。
 * 当たるものが無ければ、お金・税金・ビジネスの記事だけジャンルの最新ニュース一覧を返す。それ以外は空。
 */
export function readNextForArticle(article: ArticleSummary, exclude: Iterable<string> = [], limit = 1): ReadNextLink[] {
  const skip = new Set(exclude);
  // 法人・企業向けの記事（lib/offers.ts の isB2BArticle）には、個人向けのサーバー・副業の案内を出さない
  const noConsumer = isB2BArticle(article) && !matchesHostingTopic(article);
  const roundup = isRoundupArticle(article);
  const scored = CANDIDATES.flatMap((candidate, order) => {
    if (skip.has(candidate.href)) return [];
    if (candidate.side && roundup) return [];
    if (candidate.consumer && noConsumer) return [];
    if (candidate.genres && !candidate.genres.includes(article.genreSlug)) return [];
    const score = relevanceScore(candidate.match, article);
    return score >= MIN_SCORE ? [{ candidate, score, order }] : [];
  }).sort((a, b) => b.score - a.score || a.order - b.order);

  const links: ReadNextLink[] = [];
  for (const { candidate } of scored) {
    const link = resolveReadNext(candidate.href);
    if (link) links.push(link);
    if (links.length >= limit) return links;
  }
  const money = MONEY_GENRES.has(article.genreSlug) || (article.genreSlug === "public" && MONEY_TITLE.test(article.title));
  const href = `/genres/${article.genreSlug}`;
  if (links.length === 0 && money && article.genre && !skip.has(href)) {
    links.push({ href, kind: "category", label: `${article.genre}の最新ニュース`, note: "仕事とお金に関わる発表を、新しい順にまとめて読めます。" });
  }
  return links;
}

/** 本文途中の「あわせて読みたい」（関連記事1本）の候補にする期間 */
const INLINE_READ_DAYS = 30;
/** 全記事に付くような語・社名だけのタグでは、同じ話題と言えない */
const GENERIC_TERMS = new Set(
  ["AI", "AIツール", "AIニュース", "生成AI", "人工知能", "テクノロジー", "AIエージェント", "LLM", "OpenAI", "Microsoft", "Google", "NVIDIA", "Meta", "Anthropic", "Hugging Face", "DeepSeek", "ChatGPT", "Gemini", "Claude", "使い方", "料金", "無料", "日本語", "とは", "開発", "アップデート", "ニュース", "発表", "調査", "ビジネス", "デザイン", "提携", "業務提携", "連携", "協定", "地域", "イベント", "キャンペーン", "セール", "新製品", "新サービス"].map((w) => w.toLowerCase()),
);

/** タグ・キーワードごとの記事数。多くの記事に付く語（「金融」「提携」「デザイン」や発表元の名前）は話題の手がかりにしない */
const termCounts = new WeakMap<object, Map<string, number>>();
function countsOf(candidates: ArticleSummary[]): Map<string, number> {
  let counts = termCounts.get(candidates);
  if (!counts) {
    counts = new Map();
    for (const item of candidates) {
      for (const word of new Set([...(item.tags ?? []), ...(item.keywords ?? [])].map((w) => w.trim().toLowerCase()))) {
        counts.set(word, (counts.get(word) ?? 0) + 1);
      }
    }
    termCounts.set(candidates, counts);
  }
  return counts;
}

function escapeRegex(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 本文の途中に1本だけ出す関連記事。「あわせて読みたい」のガイドと同じ基準点（MIN_SCORE）を使う。
 * 元の記事の具体的なタグ・キーワード（多くの記事に付く語は除く）が、候補の見出しに出る（4点）か、タグと要約の両方に出るものだけ。
 * 当たった語が2つ以上のものだけ。同じ企業の発表なら1点足して先に出す（企業だけでは当てない）。直近30日・同じジャンルの記事から、点数の高い順・新しい順。
 * 当たる記事が無ければ何も出さない（関係のない記事で埋めない）。
 */
export function inlineReadForArticle<T extends ArticleSummary>(
  article: ArticleSummary,
  candidates: T[],
  companySlugOf: (a: ArticleSummary) => string,
  exclude: Iterable<string> = [],
  now = Date.now(),
): T | undefined {
  const counts = countsOf(candidates);
  // 全記事の1.5%（最低5本）を超えて付いている語は、話題ではなくジャンルや発表元の名前とみなす
  const maxCount = Math.max(5, Math.round(candidates.length * 0.015));
  const terms = [...new Set([...(article.tags ?? []), ...(article.keywords ?? [])].map((w) => w.trim()))].filter(
    (w) => w.length >= 2 && !GENERIC_TERMS.has(w.toLowerCase()) && (counts.get(w.toLowerCase()) ?? 0) <= maxCount,
  );
  if (terms.length === 0) return undefined;
  const pattern = terms.map(escapeRegex).join("|");
  const skip = new Set([article.slug, ...exclude]);
  const company = companySlugOf(article);
  const since = now - INLINE_READ_DAYS * 86_400_000;
  let best: { item: T; score: number; time: number } | undefined;
  for (const item of candidates) {
    if (skip.has(item.slug) || item.genreSlug !== article.genreSlug) continue;
    // 地震・津波の速報とそれ以外の記事は混ぜない（年金の記事の関連に【地震】が出ていた。2026-11-03）
    if (isDisasterArticle(item) !== isDisasterArticle(article)) continue;
    const time = Date.parse(item.publishedAt);
    if (!Number.isFinite(time) || time < since || time > now) continue;
    let score = relevanceScore(pattern, item);
    const sameCompany = company !== "other" && companySlugOf(item) === company;
    if (score > 0 && sameCompany) score += 1;
    if (score < MIN_SCORE) continue;
    // 語が1つ当たっただけでは偶然のことがある（同じ発表元でも別の話題がある）。2語以上の裏付けを求める
    const text = [item.title, item.seoTitle ?? "", ...(item.tags ?? []), ...(item.keywords ?? []), item.lede ?? "", item.description ?? "", ...(item.highlights ?? [])].join(" ").toLowerCase();
    if (terms.filter((term) => text.includes(term.toLowerCase())).length < 2) continue;
    if (!best || score > best.score || (score === best.score && time > best.time)) best = { item, score, time };
  }
  return best?.item;
}
