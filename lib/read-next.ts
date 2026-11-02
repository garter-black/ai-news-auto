import fs from "@/lib/content-fs";
import path from "node:path";
import type { ArticleSummary } from "@/lib/articles";
import { CHOOSE_GUIDES } from "@/lib/choose-data";
import { relevanceScore } from "@/lib/offers";

/**
 * 記事に合う広告（lib/offers.ts）が無いときに出す「あわせて読みたい」。
 * 関係の薄い広告で枠を埋める代わりに、自前の選び方ガイド（/choose）・比較（/compare）・使い方（/guides）へ案内する。
 * 読者（20〜50代の働く男性：仕事・お金・副業・独立・AIツール）に合わせて、話題ごとの行き先を決めている。
 * 題名と説明は各ページのデータから読むので、ページが無くなれば自動で候補から外れる。
 */

export type ReadNextLink = {
  href: string;
  kind: "choose" | "compare" | "guide";
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
  { href: "/guides/chatgpt-smartphone", match: "スマホ|スマートフォン|iPhone|Android|Pixel|Galaxy" },
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

/** どれにも当たらないときの行き先（ジャンルごと）。読者の関心が高く、どの記事から読んでも役に立つもの */
const DEFAULT_BY_GENRE: Record<string, string> = {
  ai: "/guides/chatgpt-how-to-use",
  "tech-gadget": "/guides/chatgpt-smartphone",
  business: "/choose/ai-career",
  public: "/choose/ai-career",
};
const DEFAULT_HREF = "/guides/chatgpt-free-plan";

/** 「あわせて読みたい」に出す最低点。タグ（2点）だけでも当てる。広告ではないので広告枠より緩い */
const MIN_SCORE = 2;

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
 * 記事に合うガイドを関連の高い順に返す。exclude は同じページに既に出ているリンク（重複させない）。
 * 当たるものが無ければジャンルの既定のガイドを1つ返す。
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
  for (const href of [DEFAULT_BY_GENRE[article.genreSlug] ?? DEFAULT_HREF, DEFAULT_HREF, "/choose/ai-career"]) {
    if (links.length >= limit) break;
    if (skip.has(href) || links.some((l) => l.href === href)) continue;
    const link = resolveReadNext(href);
    if (link) links.push(link);
  }
  return links;
}
