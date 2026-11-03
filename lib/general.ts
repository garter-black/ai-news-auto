import type { ArticleSummary } from "@/lib/articles";
import { listUsability } from "@/lib/verdict";

/**
 * 「一般の人に関係あるか」の目安。トップやストーリーで、企業向け・開発者向けの話より
 * 生活や仕事で今日から使える話を先に出すために使う。
 */
const EVERYDAY =
  /ChatGPT|Gemini|Claude|Copilot|iPhone|Android|Mac|Windows|アプリ|無料|画像|動画|音声|翻訳|議事録|検索|スマホ|写真|音楽|生活|家庭|一般/;
const ENTERPRISE =
  /Azure|Foundry|SQL|Cosmos|DB|半導体|ファブ|データセンター|API|SDK|学習効率|オプティマイザ|デコーディング|パラメータ|ベンチマーク|導入事例|全社|法務|金融|IT運用|障害対応|基盤/;

export function generalScore(article: ArticleSummary): number {
  const text = `${article.title} ${article.tags.join(" ")} ${article.verdict?.audience ?? ""}`;
  let score = 0;
  if (EVERYDAY.test(text)) score += 3;
  if (ENTERPRISE.test(text)) score -= 3;
  if (/一般|誰でも|ユーザー/.test(article.verdict?.audience ?? "")) score += 2;
  if (/開発者|エンジニア|企業|法人|IT管理/.test(article.verdict?.audience ?? "")) score -= 2;
  const u = listUsability(article);
  if (u) {
    score += u === "go" ? 2 : u === "partial" ? 0 : -1;
    if (article.verdict?.free) score += 1;
  }
  if (!article.hasEasy) score -= 5;
  return score;
}

/** 新しさを保ったまま、一般向けの話を選ぶ（直近 pool 本から上位 n 本、表示は新しい順） */
export function pickGeneral(articles: ArticleSummary[], n: number, pool = 40): ArticleSummary[] {
  const recent = articles.filter((a) => a.type !== "roundup").slice(0, pool);
  const chosen = new Set(
    [...recent]
      .map((a, i) => ({ a, s: generalScore(a) - i * 0.08 }))
      .sort((x, y) => y.s - x.s)
      .slice(0, n)
      .map((x) => x.a.slug),
  );
  return recent.filter((a) => chosen.has(a.slug));
}

/** 「かんたん」版の「ひとことで言うと」段落（索引を作るときに取り出し済み。無ければリード文） */
export function easyGist(article: ArticleSummary): string {
  return article.easyGist || article.lede.trim().replace(/\s*\n+\s*/g, "");
}
