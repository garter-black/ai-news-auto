import type { ArticleSummary } from "@/lib/articles";
import { isNewsOutletChannel, TRENDS } from "@/lib/news-outlets";

// クライアント部品からも読めるよう、articles.ts（fs を使う）は型だけ読み込む
const isWeb = (a: ArticleSummary) => a.sourceType === "web";

/**
 * 記事を「どの企業の発表か」で分類する。
 *
 * AIニュースの読者は「OpenAIの新しいやつ」「Googleの話」のように企業単位で情報を探す。
 * 色で企業を見分けられれば、見出しを読む前に目当ての記事に目が止まる（前注意的処理）。
 * 解説系チャンネル（Matthew Berman など）の記事も、話題の企業で分類する。
 */
export type Company = {
  slug: string;
  name: string;
  /** 一覧の色見本に使う1〜2文字 */
  mark: string;
  /** 背景色（白文字が読めるコントラストを確保した色） */
  color: string;
  pattern: RegExp;
};

export const COMPANIES: Company[] = [
  {
    slug: "openai",
    name: "OpenAI",
    mark: "O",
    color: "#10a37f",
    pattern: /OpenAI|ChatGPT|GPT-|Codex|Sora\b|\bo3\b/i,
  },
  {
    slug: "anthropic",
    name: "Anthropic",
    mark: "A",
    color: "#c15f3c",
    pattern: /Anthropic|Claude/i,
  },
  {
    slug: "google",
    name: "Google",
    mark: "G",
    color: "#1a73e8",
    pattern:
      /Google|グーグル|DeepMind|Gemini|Gemma|Pixel|Android|AlphaFold|Antigravity|Nano Banana|SynthID|Suncatcher/i,
  },
  {
    slug: "nvidia",
    name: "NVIDIA",
    mark: "N",
    color: "#5a8f00",
    pattern:
      /NVIDIA|エヌビディア|RTX|CUDA|Blackwell|Vera Rubin|DGX|NVLink|Nemotron|GR00T|GROOT|Alpamayo|Spectrum-X|DOCA/i,
  },
  {
    slug: "microsoft",
    name: "Microsoft",
    mark: "M",
    color: "#0067b8",
    pattern:
      /Microsoft|マイクロソフト|Azure|Copilot(?![A-Za-z])|GitHub|\.NET|TypeScript|VS ?Code|Visual Studio|Windows|Foundry|Work IQ|ZoomIt|Fabric/i,
  },
  {
    slug: "meta",
    name: "Meta",
    mark: "Me",
    color: "#0866ff",
    pattern: /\bMeta\b|米メタ|Llama/,
  },
  {
    slug: "huggingface",
    name: "Hugging Face",
    mark: "HF",
    color: "#b8860b",
    pattern: /Hugging ?Face|Transformers\.js|smolagents/i,
  },
  {
    slug: "deepseek",
    name: "DeepSeek",
    mark: "DS",
    color: "#4d6bfe",
    pattern: /DeepSeek/i,
  },
  {
    slug: "xai",
    name: "xAI",
    mark: "x",
    color: "#111111",
    pattern: /\bxAI\b|Grok/i,
  },
  {
    slug: "mistral",
    name: "Mistral AI",
    mark: "Mi",
    color: "#e2531f",
    pattern: /Mistral/i,
  },
  {
    slug: "moonshot",
    name: "Moonshot AI",
    mark: "K",
    color: "#2b2b2b",
    pattern: /Kimi|Moonshot/i,
  },
  // 2026-11-03 の点検 11: AWS・Apple・サムスン・Salesforce の AI の発表が「その他」にまとめられていた（台帳 A30）
  {
    slug: "amazon",
    name: "Amazon（AWS）",
    mark: "Am",
    color: "#a35200",
    pattern: /Amazon|アマゾン|\bAWS\b|Bedrock|Alexa|Trainium|\bKiro\b/i,
  },
  {
    slug: "apple",
    name: "Apple",
    mark: "Ap",
    color: "#3a3a3c",
    pattern: /\bApple\b|アップル|iPhone|iPad|MacBook|Mac Studio|iMac|\biOS\b|macOS|Siri|visionOS/,
  },
  {
    slug: "samsung",
    name: "Samsung",
    mark: "S",
    color: "#1428a0",
    pattern: /Samsung|サムスン|Galaxy/i,
  },
  {
    slug: "salesforce",
    name: "Salesforce",
    mark: "Sf",
    color: "#0b6fb0",
    pattern: /Salesforce|セールスフォース|Agentforce|Tableau/i,
  },
];

export const OTHER: Company = {
  slug: "other",
  name: "その他",
  mark: "・",
  color: "#465268",
  pattern: /$^/,
};

/** 公式チャンネルは、本文の話題より発信元を優先する */
const CHANNEL_COMPANY: Record<string, string> = {
  openai: "openai",
  anthropic: "anthropic",
  google: "google",
  "google-developers": "google",
  "google-deepmind": "google",
  nvidia: "nvidia",
  "microsoft-developer": "microsoft",
  "microsoft-research": "microsoft",
  "hugging-face": "huggingface",
};

const BY_SLUG = new Map(COMPANIES.map((c) => [c.slug, c]));

/**
 * 見出しの主語（最初の「、」「が」の前。かっこの中は見ない）。scripts/labels.py の subject_of と同じ。
 * 「Qodo、Codex向けプラグインを公開」の主語は Qodo（OpenAI ではない）
 */
export function subjectOf(title: string): string {
  const t = (title ?? "").normalize("NFKC").trim();
  let depth = 0;
  for (let i = 0; i < Math.min(t.length, 40); i++) {
    const ch = t[i];
    if ("「『(（".includes(ch)) depth++;
    else if ("」』)）".includes(ch) && depth > 0) depth--;
    else if (depth === 0 && (ch === "、" || ch === "が")) return t.slice(0, i);
  }
  return "";
}

/** 主語が会社・団体の名前らしいか（英字・カタカナの名前、または会社・団体を表す言葉） */
const ENTITY_LIKE = /[A-Za-z][A-Za-z0-9]|[ァ-ヴー]{3,}|(?:社|大学|大|省|庁|政府|研究所|機構|銀行|協会|グループ|など|ら\d+者|CEO|氏)$/;

/** 文章中で最も手前に出てくる企業（見出しの主語になりやすい） */
function firstMention(text: string): Company | undefined {
  let best: { company: Company; index: number } | undefined;
  for (const company of COMPANIES) {
    const match = company.pattern.exec(text);
    if (match && (!best || match.index < best.index)) {
      best = { company, index: match.index };
    }
  }
  return best?.company;
}

const cache = new Map<string, Company>();

export function companyOf(article: ArticleSummary): Company {
  const cached = cache.get(article.slug);
  if (cached) return cached;

  // 主語の会社（見出しの「だれが」）。主語が一覧に無い会社なら「その他」（発信元が OpenAI の公式ブログでも、Qodo の発表は OpenAI にしない。台帳 A30）
  const subject = subjectOf(article.title);
  const bySubject = subject ? (firstMention(subject) ?? (ENTITY_LIKE.test(subject) ? OTHER : undefined)) : undefined;
  const company =
    // 記事 JSON の companySlug（scripts/label_check.py が Haiku の確認で直したもの）が最優先
    (article.companySlug ? getCompany(article.companySlug) : undefined) ??
    // 主語が先（OpenAI の公式チャンネルの動画でも、Uber の導入事例は Uber＝その他。Haiku の確認で 30 本あまりずれていた）
    bySubject ??
    BY_SLUG.get(CHANNEL_COMPANY[article.channelSlug] ?? "") ??
    // 公式ブログ・プレスリリースは発信元名（「OpenAI公式ブログ」など）を優先する
    (isWeb(article) ? firstMention(article.sourceName ?? "") : undefined) ??
    firstMention(article.title) ??
    firstMention(article.tags.join(" ")) ??
    firstMention(article.description) ??
    OTHER;

  cache.set(article.slug, company);
  return company;
}

export function getCompany(slug: string): Company | undefined {
  return slug === OTHER.slug ? OTHER : BY_SLUG.get(slug);
}

/** 記事数の多い順。トップの企業チップと /companies の一覧に使う */
export function companyCounts(articles: ArticleSummary[]): { company: Company; count: number }[] {
  const counts = new Map<string, number>();
  for (const article of articles) {
    const { slug } = companyOf(article);
    counts.set(slug, (counts.get(slug) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([slug, count]) => ({ company: getCompany(slug) ?? OTHER, count }))
    .sort((a, b) => {
      // 「その他」は常に最後
      if (a.company.slug === OTHER.slug) return 1;
      if (b.company.slug === OTHER.slug) return -1;
      return b.count - a.count;
    });
}

/** サムネイルが無いときの代替表示に使う、発信元名と企業色 */
export type ThumbSource = { label: string; color: string; mark: string; /** 札の言葉（報道機関の記事は「報道」。台帳 W12） */ badge?: string };

/** web 記事だけ発信元入りの代替表示にする（動画記事は従来どおり undefined） */
export function thumbSource(article: ArticleSummary): ThumbSource | undefined {
  if (!isWeb(article)) return undefined;
  const company = companyOf(article);
  const isOther = company.slug === OTHER.slug;
  return {
    label: article.sourceName || article.channel,
    color: isOther ? "#1d1d22" : company.color,
    mark: isOther ? "" : company.mark,
    badge: sourceBadge(article),
  };
}

/**
 * 出典の種類の札。報道機関の記事（lib/news-outlets.ts）を「公式発表」と呼ばない（台帳 W12）。
 * 話題のまとめ（trends）は報道と公式が混ざるので「公開情報」
 */
export function sourceBadge(article: Pick<ArticleSummary, "channelSlug">): string {
  if (article.channelSlug === TRENDS) return "公開情報";
  return isNewsOutletChannel(article.channelSlug) ? "報道" : "公式発表";
}
