import data from "@/content/weekly.json";
import { getArticleSummary } from "@/lib/articles";
import { companyOf, OTHER } from "@/lib/companies";
import { displayHeadline, gateHeadline } from "@/lib/headline";

/**
 * 今週のAIニュースまとめ（/weekly）。scripts/weekly.py が毎週月曜 07:00 JST に content/weekly.json へ足す。
 */
export type WeeklyItem = {
  slug: string;
  title: string;
  company: string;
  publishedAt: string;
  /** 何が起きた（1文） */
  what: string;
  /** なぜ大事（1文） */
  why: string;
};

export type Week = {
  /** ISO 週（2026-43） */
  id: string;
  /** 月曜（JST） */
  start: string;
  /** 日曜（JST） */
  end: string;
  title: string;
  lede: string[];
  items: WeeklyItem[];
  articleCount: number;
  generatedAt: string;
  model: string;
};

/**
 * 表示する1本。題名は記事の見出し（h1）を題名の門番に通したもの（検索向けの「〇〇とは？日本で使える？料金も」を10本並べない。
 * 「ChatGPTのOpenAIが」も出さない。台帳 A25）。会社は記事ページと同じ判定（lib/companies.ts）。記事が無いときは保存した値を門番に通す
 */
export function weeklyItemForDisplay(item: WeeklyItem): WeeklyItem {
  const article = getArticleSummary(item.slug);
  if (!article) return { ...item, title: displayHeadline(gateHeadline(item.title)) };
  const company = companyOf(article);
  return {
    ...item,
    title: displayHeadline(gateHeadline(article.title, article.title)),
    company: company.slug === OTHER.slug ? item.company : company.name,
  };
}

const rawWeeks = ((data as unknown as { weeks: Week[] }).weeks ?? [])
  .filter((week) => /^\d{4}-\d{2}$/.test(week.id))
  .sort((a, b) => b.id.localeCompare(a.id));

// 記事の索引は読み込みの時点ではまだ使えないことがある（Cloudflare の仮想ファイル）。初めて使うときに表示用に直す
let displayWeeks: Week[] | null = null;
function weeks(): Week[] {
  if (displayWeeks === null) displayWeeks = rawWeeks.map((week) => ({ ...week, items: week.items.map(weeklyItemForDisplay) }));
  return displayWeeks;
}

/** 新しい週から */
export function getAllWeeks(): Week[] {
  return weeks();
}

export function getWeek(id: string): Week | undefined {
  return weeks().find((week) => week.id === id);
}

/** 前後の週（古い・新しい） */
export function neighborWeeks(id: string): { older?: Week; newer?: Week } {
  const list = weeks();
  const index = list.findIndex((week) => week.id === id);
  if (index < 0) return {};
  return { newer: list[index - 1], older: list[index + 1] };
}

/** 「10月19日〜25日」「12月28日〜1月3日」 */
export function weekRangeLabel(week: Week): string {
  const [, sm, sd] = week.start.split("-").map(Number);
  const [, em, ed] = week.end.split("-").map(Number);
  return sm === em ? `${sm}月${sd}日〜${ed}日` : `${sm}月${sd}日〜${em}月${ed}日`;
}

/** 週の公開日時（月曜 07:00 JST の配信に合わせる） */
export function weekPublishedAt(week: Week): string {
  const [y, m, d] = week.end.split("-").map(Number);
  // 日曜の翌日 07:00 JST = 日曜 22:00 UTC
  return new Date(Date.UTC(y, m - 1, d, 22, 0, 0)).toISOString();
}
