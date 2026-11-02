import fs from "@/lib/content-fs";
import path from "node:path";
import type { ArticleSummary } from "@/lib/articles";
import { CHOOSE_GUIDES } from "@/lib/choose-data";
import { MIN_SCORE as OFFER_MIN_SCORE, relevanceScore } from "@/lib/offers";

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

type Candidate = { href: string; match: string; genres?: string[] };

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
  { href: "/compare/vps-dify-n8n", match: "AIエージェント|(?<!サイバー)エージェント|ワークフロー|セルフホスト|VPS" },
  { href: "/compare/image-generation", match: "画像生成|イラスト|Stable Diffusion|Midjourney|デザインツール|Figma" },
  { href: "/compare/video-editing", match: "動画生成|動画編集|YouTube|ショート動画|配信者" },
  { href: "/compare/ai-voice-recorder", match: "議事録|文字起こし|録音|ボイスレコーダー|音声認識" },
  { href: "/compare/ai-writing-tools", match: "文章|ライティング|記事作成|執筆|校正" },
  { href: "/compare/ai-english", match: "英語|英会話|翻訳|語学|留学" },
  { href: "/compare/local-llm-pc", match: "ローカルLLM|ローカルAI|GPU|RTX|GeForce|VRAM|NVIDIA|半導体|ゲーミングPC", genres: ["ai", "tech-gadget"] },
  { href: "/compare/ai-mini-pc", match: "ミニPC|NPU|Copilot\\+ ?PC" },
  { href: "/compare/security-software", match: "詐欺|フィッシング|不正アクセス|情報漏え?い|情報流出|個人情報|サイバー(?!エージェント)|ランサム|脆弱性|ウイルス|マルウェア|なりすまし|セキュリティ|攻撃者|サイバー攻撃|侵入|ハッキング" },
  { href: "/compare/kids-programming", match: "子ども向け|小学生|中学生|プログラミング教育|プログラミング教室|STEAM" },
  // 働き方・お金
  { href: "/choose/internet-for-ai", match: "光回線|ネット回線|WiMAX|Wi-?Fi|格安SIM|スマホ料金|携帯料金|通信料金|5G|テレワーク|リモートワーク|在宅勤務" },
  { href: "/compare/rental-server", match: "副業|複業|フリーランス|個人事業|独立|起業|開業|創業|ホームページ|Webサイト|ブログ|WordPress|集客|ネットショップ|ECサイト" },
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
  const scored = CANDIDATES.flatMap((candidate, order) => {
    if (skip.has(candidate.href)) return [];
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
