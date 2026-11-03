import { isWeb, sourceHref, sourceLabel, type FullArticle } from "@/lib/articles";
import { COMPANIES, OTHER, companyOf, type Company } from "@/lib/companies";
import { renderMarkdown, slugifyHeading } from "@/lib/markdown";
import { STATUS_LABEL, isUnknownValue, japanLabel, verdictOf } from "@/lib/verdict";
import { questionFor } from "./questions";

/**
 * 「引き算のAI」記事ページ（試作）の表示用データ。
 * 10秒版＝結論1文＋要点3つ、1分版＝かんたん版、全文＝本文（章見出しを読者の問いに）。
 * 数字や事実は記事 JSON にあるものだけを使い、無いものは出さない。
 */

export type Section = { id: string; question: string; original: string; html: string; update?: string };

export type TimelineEvent = { date: string; label: string; target?: string };

export type ConceptView = {
  slug: string;
  title: string;
  conclusion: string;
  points: string[];
  /** 対象・提供状況・料金（判定が無い記事は空） */
  fit: string[];
  minuteHtml: string;
  minuteMinutes: number;
  sections: Section[];
  fullMinutes: number;
  bodyChars: number;
  timeline: TimelineEvent[];
  company: { name: string; color: string; isOther: boolean };
  label: string;
  photo: string;
  source: { name: string; href: string; cta: string; isWeb: boolean };
  publishedLabel: string;
  stampIso: string;
  stampPrefix: string;
  disclaimer: string;
};

// 単位付きの数字だけを拾う（年号・日付・型番は拾わない）
const FIG =
  /(?:[$＄]\s?\d[\d,.]*(?:\/1?[MK])?|\d[\d,.]*\s?(?:%|％|倍|ドル|円|ポイント|pt|万|億|兆|分の\d+|平米|DK|LDK)(?:以上|以下|未満)?)/g;

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** 文中の数字を <b class="cx-fig"> で囲んだ HTML（エスケープ済み） */
function figures(text: string): string {
  return escapeHtml(text).replace(FIG, (m) => `<b class="cx-fig">${m}</b>`);
}

const jst = (iso: string, opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", ...opts }).format(new Date(iso));

/** 「10月26日」。時刻が UTC 0時ちょうど（＝日付だけの発表）なら時刻は出さない */
function publishedLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = jst(iso, { month: "long", day: "numeric" });
  const dateOnly = d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0;
  return dateOnly ? day : `${day} ${jst(iso, { hour: "2-digit", minute: "2-digit" })}`;
}

function firstSentence(text: string): string {
  const m = /^.+?[。！？]/.exec(text.trim());
  return (m ? m[0] : text).trim();
}

/** markdown を「## 見出し」ごとに分ける（見出しの前の文章は heading 空） */
function splitSections(markdown: string): { heading: string; body: string }[] {
  const out: { heading: string; body: string }[] = [];
  let current = { heading: "", body: "" };
  let inCode = false;
  for (const line of (markdown ?? "").split("\n")) {
    if (/^\s*```/.test(line)) inCode = !inCode;
    const m = !inCode && /^##\s+(.+?)\s*$/.exec(line);
    if (m) {
      if (current.heading || current.body.trim()) out.push(current);
      current = { heading: m[1].replace(/[*_`]/g, "").trim(), body: "" };
    } else {
      current.body += `${line}\n`;
    }
  }
  if (current.heading || current.body.trim()) out.push(current);
  return out;
}

const UPDATE = /^続報（(\d{1,2}月\d{1,2}日)）[：:]?\s*(.*)$/;

/** 表の列のうち、全行が同じ値（「元記事」だけの出典列など）を消す。引き算の一つ */
function dropFlatColumns(html: string): string {
  return html.replace(/<table>([\s\S]*?)<\/table>/g, (all, inner: string) => {
    const rows = [...inner.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((r) => [...r[1].matchAll(/<t[hd][^>]*>([\s\S]*?)<\/t[hd]>/g)]);
    if (rows.length < 3) return all;
    const body = rows.slice(1);
    const width = rows[0].length;
    const drop = new Set<number>();
    for (let i = 1; i < width; i++) {
      const values = new Set(body.map((cells) => cells[i]?.[1].trim()));
      if (values.size === 1) drop.add(i);
    }
    if (drop.size === 0) return all;
    let index = -1;
    return `<table>${inner.replace(/<tr>([\s\S]*?)<\/tr>/g, (_row, cells: string) => {
      index = -1;
      return `<tr>${cells.replace(/<(t[hd])([^>]*)>([\s\S]*?)<\/\1>/g, (cell) => (drop.has(++index) ? "" : cell))}</tr>`;
    })}</table>`;
  });
}

/** 表は横スクロールの枠で包み、数字を等幅に揃える */
function wrapTables(html: string): string {
  return dropFlatColumns(html).replace(/<table>[\s\S]*?<\/table>/g, (t) => `<div class="cx-table">${t}</div>`);
}

/** 本文の h2 を落とした HTML（見出しは問いとして画面側で組む） */
async function renderSection(markdown: string, affiliateUrls: string[]): Promise<string> {
  return wrapTables(await renderMarkdown(markdown, affiliateUrls));
}

/**
 * 表紙の企業色。公式ブログ・プレスリリースは発信元名で決め、本文の話題（「Word」「Claude」など）では決めない。
 * サイトの companyOf は本文の話題で拾うので、Apple の発表が Microsoft 色になることがある
 */
function conceptCompany(article: FullArticle): Company {
  if (!isWeb(article)) return companyOf(article);
  return COMPANIES.find((c) => c.pattern.test(article.sourceName ?? "")) ?? OTHER;
}

/** 最初の章の見出し（新しい記事は「概要」、古い記事は「ひとことで言うと」） */
const FIRST_HEADINGS = new Set(["概要", "ひとことで言うと"]);

const minutes = (chars: number, perMinute: number) => Math.max(1, Math.round(chars / perMinute));

export async function buildConceptView(article: FullArticle): Promise<ConceptView> {
  const affiliateUrls = (article.affiliates ?? []).map((a) => a.url);
  const verdict = verdictOf(article);
  const company = conceptCompany(article);
  const isOther = company.slug === OTHER.slug;

  // 結論の1文：判定のひとこと → かんたん版の「ひとことで言うと」→ リード文の1文目
  const conclusion = (verdict?.take?.trim() || article.easyGist?.trim() || firstSentence(article.lede)).replace(/\s+/g, " ");

  const fitParts: string[] = [];
  if (verdict) {
    if (!isUnknownValue(verdict.audience)) fitParts.push(`${verdict.audience}向け`);
    const japan = japanLabel(verdict);
    fitParts.push(verdict.japan === "ok" && verdict.status === "available" ? `${STATUS_LABEL[verdict.status]}（日本から利用可）` : `${STATUS_LABEL[verdict.status]}・${japan}`);
    if (!isUnknownValue(verdict.price)) fitParts.push(verdict.price);
  }

  // 1分版：かんたん版。無ければリード文と本文の「ひとことで言うと」
  const easy = article.easy?.trim() ?? "";
  let minuteMd = "";
  if (easy) {
    minuteMd = splitSections(easy)
      .filter((s) => !UPDATE.test(s.heading))
      .map((s) => (FIRST_HEADINGS.has(s.heading) || !s.heading ? s.body : `## ${s.heading}\n${s.body}`))
      .join("\n");
  } else {
    const gist = splitSections(article.body).find((s) => FIRST_HEADINGS.has(s.heading) || s.heading === "この動画で語られていること");
    minuteMd = `${article.lede}\n\n${gist?.body ?? ""}`;
  }
  const minuteHtml = await renderSection(minuteMd, affiliateUrls);
  // 1分版の見出しも問いの形に揃える（かんたん版の見出しはもともと問い）
  const minuteWithQs = minuteHtml.replace(/<h2>([\s\S]*?)<\/h2>/g, (_m, h: string) => `<h3 class="cx-q-sm">${escapeHtml(questionFor(article.slug, h.replace(/<[^>]+>/g, "")))}</h3>`);

  // 全文：「概要」（古い記事は「ひとことで言うと」）は10秒版と重なるので外す。続報の章は「その後どうなった？」
  const sections: Section[] = [];
  const timeline: TimelineEvent[] = [];
  for (const s of splitSections(article.body)) {
    if (!s.heading || FIRST_HEADINGS.has(s.heading)) continue;
    const update = UPDATE.exec(s.heading);
    const id = update ? `update-${sections.length}` : slugifyHeading(s.heading) || `s-${sections.length}`;
    sections.push({
      id,
      question: update ? "その後どうなった？" : questionFor(article.slug, s.heading),
      original: update ? update[2] : s.heading,
      update: update?.[1],
      html: await renderSection(s.body, affiliateUrls),
    });
    if (update) timeline.push({ date: update[1], label: update[2] || "続報", target: id });
  }
  // 物語の線：続報があるときだけ。始点は最初の発表
  if (timeline.length > 0) {
    timeline.reverse();
    timeline.unshift({ date: jst(article.publishedAt, { month: "long", day: "numeric" }), label: "最初の発表" });
  }

  const href = sourceHref(article);
  return {
    slug: article.slug,
    title: article.title,
    conclusion,
    points: article.highlights.slice(0, 3).map(figures),
    fit: fitParts,
    minuteHtml: minuteWithQs,
    minuteMinutes: minutes(minuteMd.length, 500),
    sections,
    fullMinutes: minutes(article.body.length, 600),
    bodyChars: article.body.replace(/\s+/g, "").length,
    timeline,
    company: { name: isOther ? "" : company.name, color: isOther ? "#d92d20" : company.color, isOther },
    label: article.type === "roundup" ? `${article.sourceCount}本を統合` : timeline.length > 0 ? "続報" : "速報",
    photo: /^https?:\/\//.test(article.thumbnail) ? article.thumbnail : "",
    source: {
      name: sourceLabel(article),
      href,
      cta: isWeb(article) ? "公式発表を読む" : "元の動画を見る",
      isWeb: isWeb(article),
    },
    publishedLabel: publishedLabel(article.publishedAt),
    stampIso: article.updatedAt || article.generatedAt,
    stampPrefix: article.updatedAt ? "更新" : "掲載",
    disclaimer: article.disclaimer,
  };
}
