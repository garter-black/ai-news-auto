import { compactJa } from "@/lib/calc-math";
import { formatMoney } from "@/lib/money";
import type { Fx, PlanProduct } from "@/lib/plan-prices";

/**
 * 記事の数字の図（記事 JSON の charts 欄。scripts/article_charts.py が記事の表から作る）。
 *
 * - 前と今（before_after）・推移（series）・料金の段（ladder）の3種類
 * - 比べられる本物の数字が2つ以上あるときだけ出す。棒はかならず0から（長さで差を大きく見せない）
 * - 表示する値は記事に書いてある文字のまま。棒の長さだけを数字から決める
 * - JavaScript を使わない HTML＋インライン SVG を本文の該当する章の下に差し込む（ページの読み込みを遅くしない）
 * - 色はサイトの色の約束（--accent / --line-strong / --surface-2）なので、暗い画面でもそのまま読める
 */

export type ChartItem = { label: string; value: number; display: string; highlight?: boolean; note?: string };
export type ChartSource = { name: string; url?: string; checkedAt?: string };

export type BeforeAfterChart = {
  kind: "before_after";
  title: string;
  unit?: string;
  groups: { label: string; items: ChartItem[] }[];
  afterHeading?: string | null;
  source?: ChartSource;
};
export type SeriesChart = {
  kind: "series";
  title: string;
  unit?: string;
  points: ChartItem[];
  afterHeading?: string | null;
  source?: ChartSource;
};
export type LadderChart = { kind: "ladder"; product: string; highlight?: string; afterHeading?: string | null };
export type ArticleChart = BeforeAfterChart | SeriesChart | LadderChart;

/** 表示する図（HTML）と、差し込む章 */
export type ChartFigure = { html: string; afterHeading: string | null; kind: ArticleChart["kind"] };

const isReal = (item: ChartItem | undefined): item is ChartItem =>
  Boolean(item && typeof item.value === "number" && Number.isFinite(item.value) && item.value >= 0 && item.display?.trim() && item.label?.trim());

function esc(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function pct(value: number, max: number): string {
  if (!(max > 0) || value <= 0) return "0%";
  // 0 でない値は細くても見えるように最低 0.8%
  return `${Math.max(0.8, Math.round((value / max) * 1000) / 10)}%`;
}

function bar(value: number, max: number): string {
  return (
    `<svg class="sc-bar" width="100%" height="10" aria-hidden="true" focusable="false">` +
    `<rect class="sc-track" x="0" y="0" width="100%" height="10" rx="2"></rect>` +
    `<rect class="sc-fill" x="0" y="0" width="${pct(value, max)}" height="10" rx="2"></rect></svg>`
  );
}

function row(item: ChartItem, max: number): string {
  return (
    `<div class="sc-row${item.highlight ? " sc-hl" : ""}">` +
    `<div class="sc-row-text"><span class="sc-label">${esc(item.label)}</span><span class="sc-value">${esc(item.display)}${item.note ? `<span class="sc-mark">${esc(item.note)}</span>` : ""}</span></div>` +
    bar(item.value, max) +
    `</div>`
  );
}

/** 前→今の変化（数字から計算。% の値は「ポイント」で） */
export function deltaText(before: number, after: number, unit = ""): string {
  const diff = after - before;
  if (diff === 0) return "変わらず";
  const sign = diff > 0 ? "+" : "−";
  if (unit === "%") {
    return `${sign}${Math.round(Math.abs(diff) * 100) / 100}ポイント`;
  }
  const amount = `${sign}${compactJa(Math.abs(diff)).replace(/^-/, "")}${unit}`;
  if (before <= 0) return amount;
  const rate = (diff / before) * 100;
  const rateText = Math.abs(rate) >= 100 ? `${Math.round((after / before) * 10) / 10}倍` : `${sign}${Math.round(Math.abs(rate) * 10) / 10}%`;
  return `${amount}（${rateText}）`;
}

function figure(kind: string, title: string, inner: string, foot: string, side = ""): string {
  return (
    `<figure class="sc-chart not-prose" data-chart="${kind}">` +
    `<figcaption class="sc-group-head"><span class="sc-title">${esc(title)}</span>${side ? `<span class="sc-delta">${esc(side)}</span>` : ""}</figcaption>` +
    inner +
    `<p class="sc-source text-note">${foot}</p>` +
    `</figure>`
  );
}

function sourceLine(source: ChartSource | undefined, extra: string[] = []): string {
  const parts: string[] = [];
  if (source?.name) {
    const name = source.url ? `<a href="${esc(source.url)}" rel="noopener" target="_blank">${esc(source.name)}</a>` : esc(source.name);
    parts.push(`出典：${name}${source.checkedAt ? `（${esc(source.checkedAt)}確認）` : ""}`);
  }
  parts.push(...extra);
  parts.push("棒の長さは0からの大きさです。");
  return parts.join("。").replace(/。。/g, "。");
}

export function renderBeforeAfter(chart: BeforeAfterChart): string | null {
  const groups = (chart.groups ?? []).map((g) => ({ ...g, items: (g.items ?? []).filter(isReal) })).filter((g) => g.items.length >= 2);
  if (groups.length === 0) return null;
  const max = Math.max(...groups.flatMap((g) => g.items.map((i) => i.value)));
  if (!(max > 0)) return null;
  const single = groups.length === 1;
  // 1つだけの比較で名前が題と同じときは、変化の数字を題の横に出す
  const merged = single && groups[0].label === chart.title;
  const deltaOf = (g: (typeof groups)[number]) => (g.items.length === 2 ? deltaText(g.items[0].value, g.items[1].value, chart.unit) : "");
  const inner = groups
    .map((g) => {
      const delta = merged ? "" : deltaOf(g);
      const showLabel = !merged;
      return (
        `<div class="sc-group">` +
        (showLabel || delta
          ? `<div class="sc-group-head">${showLabel ? `<span class="sc-group-label">${esc(g.label)}</span>` : "<span></span>"}${delta ? `<span class="sc-delta">${esc(delta)}</span>` : ""}</div>`
          : "") +
        g.items.map((i) => row(i, max)).join("") +
        `</div>`
      );
    })
    .join("");
  return figure("before_after", chart.title, inner, sourceLine(chart.source, ["数字は記事の表のとおり"]), merged ? deltaOf(groups[0]) : "");
}

export function renderSeries(chart: SeriesChart): string | null {
  const points = (chart.points ?? []).filter(isReal);
  if (points.length < 2) return null;
  const max = Math.max(...points.map((p) => p.value));
  if (!(max > 0)) return null;
  const last = points.length - 1;
  const inner = `<div class="sc-group">${points.map((p, i) => row({ ...p, highlight: p.highlight ?? i === last }, max)).join("")}</div>`;
  return figure("series", chart.title, inner, sourceLine(chart.source, ["数字は記事の表のとおり"]));
}

/** 料金の段の1行（どの通貨で比べたか） */
export type LadderRow = { id: string; label: string; value: number; display: string; converted: boolean; annualOnly: boolean };

/**
 * 1つの製品の個人向けプランを、月額の安い順に並べる。
 * 全プランに円の公式価格があれば円、ドルがあればドル。混ざるときは円にそろえ、ドルしか無いプランだけ為替で換算して印を付ける。
 */
export function ladderRows(product: PlanProduct, fx: Fx): { rows: LadderRow[]; currency: "USD" | "JPY" } {
  const pick = (plan: PlanProduct["plans"][number], currency: "USD" | "JPY") =>
    plan.prices.find((p) => p.currency === currency && p.billing === "monthly") ?? plan.prices.find((p) => p.currency === currency);
  const plans = product.plans;
  const allJpy = plans.every((p) => pick(p, "JPY"));
  const allUsd = plans.every((p) => pick(p, "USD"));
  const currency: "USD" | "JPY" = allJpy ? "JPY" : allUsd ? "USD" : "JPY";
  const rows: LadderRow[] = [];
  for (const plan of plans) {
    const own = pick(plan, currency);
    if (own) {
      rows.push({
        id: plan.id,
        label: plan.name,
        value: own.amount,
        display: own.amount === 0 ? "無料" : formatMoney(own.amount, currency),
        converted: false,
        annualOnly: own.billing === "annual",
      });
      continue;
    }
    const usd = pick(plan, "USD");
    if (!usd) continue;
    const yen = usd.amount * fx.rate;
    rows.push({
      id: plan.id,
      label: plan.name,
      value: yen,
      display: `約${(Math.round(yen / 10) * 10).toLocaleString("ja-JP")}円（${formatMoney(usd.amount, "USD")}）`,
      converted: true,
      annualOnly: usd.billing === "annual",
    });
  }
  rows.sort((a, b) => a.value - b.value);
  return { rows, currency };
}

export function renderLadder(chart: LadderChart, product: PlanProduct | undefined, fx: Fx): string | null {
  if (!product) return null;
  const { rows } = ladderRows(product, fx);
  if (rows.length < 2 || rows.filter((r) => r.value > 0).length < 1) return null;
  const max = Math.max(...rows.map((r) => r.value));
  const items: ChartItem[] = rows.map((r) => ({
    label: r.label,
    value: r.value,
    display: r.display,
    highlight: r.id === chart.highlight,
    note: r.annualOnly ? "年払い時" : undefined,
  }));
  const extra: string[] = [];
  extra.push("月額・税抜き（個人向けプラン）");
  if (rows.some((r) => r.converted)) extra.push(`ドルだけのプランは1ドル=${fx.rate}円（${fx.date}、${fx.source}）で換算した目安`);
  const source: ChartSource = { name: product.sourceTitle, url: product.sourceUrl, checkedAt: product.checkedAt };
  return figure("ladder", `${product.name}の個人向けプラン（月額）`, `<div class="sc-group">${items.map((i) => row(i, max)).join("")}</div>`, sourceLine(source, extra));
}

export function renderChart(chart: ArticleChart, resolve: (product: string) => PlanProduct | undefined, fx: Fx): ChartFigure | null {
  let html: string | null = null;
  if (chart.kind === "before_after") html = renderBeforeAfter(chart);
  else if (chart.kind === "series") html = renderSeries(chart);
  else if (chart.kind === "ladder") html = renderLadder(chart, resolve(chart.product), fx);
  return html ? { html, afterHeading: chart.afterHeading ?? null, kind: chart.kind } : null;
}

// ---------------------------------------------------------------------------
// 本文の HTML への差し込み
// ---------------------------------------------------------------------------

const PRICING_HEADING = /料金|価格|値段|いくら|プラン/;
/** 計算機を置く場所の印（components/ArticleProse が分ける） */
export const CALC_SLOT = "<!--saisoku-calc-->";

function headingText(inner: string): string {
  return inner
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .trim();
}

type H2 = { start: number; text: string };

function h2s(html: string): H2[] {
  const list: H2[] = [];
  const pattern = /<h2[^>]*>([\s\S]*?)<\/h2>/gi;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(html))) list.push({ start: m.index, text: headingText(m[1]) });
  return list;
}

/**
 * 章の終わり（次の h2 の直前）の位置。heading が文字列ならその見出しの章、正規表現なら最初に合う章。
 * 見つからなければ null。
 */
export function sectionEnd(html: string, heading: string | RegExp): number | null {
  const list = h2s(html);
  const index = list.findIndex((h) => (typeof heading === "string" ? h.text === heading.trim() : heading.test(h.text)));
  if (index < 0) return null;
  return index + 1 < list.length ? list[index + 1].start : html.length;
}

function insertAt(html: string, at: number, piece: string): string {
  return html.slice(0, at) + piece + html.slice(at);
}

/** 図を本文の章の下へ差し込む。章が見つからない図は、最初の章（概要）の下に置く */
export function insertFigures(html: string, figures: ChartFigure[]): string {
  let out = html;
  for (const fig of figures) {
    const at =
      (fig.afterHeading ? sectionEnd(out, fig.afterHeading) : null) ??
      (fig.kind === "ladder" ? sectionEnd(out, PRICING_HEADING) : null) ??
      sectionEnd(out, /./);
    out = at === null ? out + fig.html : insertAt(out, at, fig.html);
  }
  return out;
}

/** 計算機の置き場所の印を、合う章の終わりに入れる。合う章が無ければそのまま（記事末に置く） */
export function markCalculatorSlot(html: string, heading: RegExp | null): string {
  if (!heading) return html;
  const at = sectionEnd(html, heading);
  return at === null ? html : insertAt(html, at, CALC_SLOT);
}
