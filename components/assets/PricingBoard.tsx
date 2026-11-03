"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { formatMoney, formatYen } from "@/lib/money";
import type { PriceRow } from "@/lib/pricing";

/* ------------------------------------------------------------------ */
/* 使い方 → トークン数の前提（控えめではなく、やや多めに見積もる）        */
/* ------------------------------------------------------------------ */

type ActivityKey = "chat" | "summary" | "blog" | "image";

const ACTIVITIES: {
  key: ActivityKey;
  label: string;
  /** 「1日」「1か月」 */
  per: "day" | "month";
  unit: string;
  input: number;
  output: number;
  note: string;
}[] = [
  { key: "chat", label: "チャットで質問する", per: "day", unit: "回", input: 500, output: 300, note: "1回≒入力500・出力300トークン" },
  { key: "summary", label: "長い資料を要約する", per: "day", unit: "本", input: 8000, output: 800, note: "1本≒入力8,000（A4で約8枚）・出力800トークン" },
  { key: "blog", label: "ブログ記事を書かせる", per: "month", unit: "本", input: 1000, output: 4000, note: "1本≒入力1,000・出力4,000トークン（約4,000字）" },
  { key: "image", label: "画像を読ませる", per: "day", unit: "枚", input: 1500, output: 300, note: "1枚≒入力1,500・出力300トークン（画像の大きさで変わる）" },
];

type Usage = Record<ActivityKey, number>;

const PRESETS: { key: string; label: string; usage: Usage }[] = [
  { key: "light", label: "少し", usage: { chat: 3, summary: 0, blog: 1, image: 0 } },
  { key: "normal", label: "ふつう", usage: { chat: 10, summary: 1, blog: 4, image: 1 } },
  { key: "heavy", label: "たくさん", usage: { chat: 50, summary: 5, blog: 20, image: 10 } },
];
const DEFAULT = PRESETS[1].usage;
const DAYS = 30;

function monthlyTokens(usage: Usage) {
  let input = 0;
  let output = 0;
  let actions = 0;
  for (const a of ACTIVITIES) {
    const times = usage[a.key] * (a.per === "day" ? DAYS : 1);
    input += times * a.input;
    output += times * a.output;
    actions += times;
  }
  return { input, output, actions };
}

/** 月の費用（ドル）。使う側の料金が記事に無ければ計算しない */
function monthlyUsd(row: PriceRow, input: number, output: number): number | null {
  if (row.input === null && input > 0) return null;
  if (row.output === null && output > 0) return null;
  return ((row.input ?? 0) * input + (row.output ?? 0) * output) / 1_000_000;
}

/** 1円未満も読める円表記（約0.05円・約1.2円・約1,500円） */
function yen(value: number): string {
  if (value <= 0) return "0円";
  if (value < 0.01) return "0.01円未満";
  if (value < 1) return `約${value.toFixed(2).replace(/0$/, "")}円`;
  if (value < 10) return `約${value.toFixed(1)}円`;
  return formatYen(value);
}

/** 月額プランを円に直す */
function planYen(row: PriceRow, rate: number): number {
  return (row.monthly ?? 0) * (row.currency === "USD" ? rate : 1);
}

/** 大きく見せる数字の部分だけ（「約1,500」） */
function yenNumber(value: number): string {
  return yen(value).replace(/円$/, "");
}

const day = new Intl.DateTimeFormat("ja-JP", { month: "numeric", day: "numeric", timeZone: "Asia/Tokyo" });

type Sort = "cost" | "company" | "newest";

/**
 * 早見表の本体。上から「いちばん安いといくら（大きな数字）」「使い方」「モデルごとの月額（棒グラフ）」
 * 「料金と日本対応の一覧」「月額プラン」。一覧はサーバーで描画済みの初期状態のまま検索エンジンにも読める。
 */
export default function PricingBoard({ rows }: { rows: PriceRow[] }) {
  const [usage, setUsage] = useState<Usage>(DEFAULT);
  const [custom, setCustom] = useState<{ input: number; output: number } | null>(null);
  const [rate, setRate] = useState(150);
  const [japanOnly, setJapanOnly] = useState(false);
  const [showLegacy, setShowLegacy] = useState(false);
  const [sort, setSort] = useState<Sort>("cost");

  const fromUsage = monthlyTokens(usage);
  const tokens = custom ? { input: custom.input * 10_000, output: custom.output * 10_000, actions: 0 } : fromUsage;
  const preset = custom ? undefined : PRESETS.find((p) => ACTIVITIES.every((a) => p.usage[a.key] === usage[a.key]));

  const apis = useMemo(
    () =>
      rows
        .filter((row) => row.kind === "api")
        .filter((row) => !japanOnly || row.japan === "ok" || row.japan === "partial")
        .map((row) => {
          const usd = monthlyUsd(row, tokens.input, tokens.output);
          return { row, usd, jpy: usd === null ? null : usd * rate };
        }),
    [rows, japanOnly, tokens.input, tokens.output, rate],
  );
  const current = apis.filter((a) => !a.row.legacy);
  const ranked = [...current].sort((a, b) => (a.jpy ?? Infinity) - (b.jpy ?? Infinity));
  const priced = ranked.filter((a) => a.jpy !== null);
  const cheapest = priced[0];
  const priciest = priced.at(-1);
  const maxJpy = priciest?.jpy ?? 0;

  const listed = useMemo(() => {
    const list = apis.filter((a) => showLegacy || !a.row.legacy);
    const cost = (v: number | null) => v ?? Infinity;
    return list.sort((a, b) => {
      if (sort === "newest") return b.row.source.publishedAt.localeCompare(a.row.source.publishedAt);
      if (sort === "company") return a.row.companyName.localeCompare(b.row.companyName) || cost(a.jpy) - cost(b.jpy);
      return cost(a.jpy) - cost(b.jpy);
    });
  }, [apis, showLegacy, sort]);

  const plans = rows
    .filter((row) => row.kind === "plan")
    .filter((row) => !japanOnly || row.japan === "ok" || row.japan === "partial")
    .sort((a, b) => (a.monthly ?? 0) - (b.monthly ?? 0));
  const cheapestPlan = rows
    .filter((row) => row.kind === "plan")
    .sort((a, b) => planYen(a, rate) - planYen(b, rate))[0];
  const legacyCount = rows.filter((row) => row.kind === "api" && row.legacy).length;
  const usageLabel = custom
    ? `入力${custom.input.toLocaleString()}万・出力${custom.output.toLocaleString()}万トークン/月の場合`
    : `${preset ? `「${preset.label}」の使い方` : "選んだ使い方"}の場合`;

  return (
    <div>
      {/* ============ 大きな数字と使い方 ============ */}
      {/* スマホは「大きな数字 → 使い方 → 補足」、広い画面は左に数字と補足・右に使い方 */}
      <section
        aria-labelledby="hero-heading"
        className="grid gap-x-12 gap-y-7 rounded-xl border border-line border-t-[3px] border-t-ink bg-paper p-5 shadow-card sm:p-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
      >
        <div className="min-w-0 lg:col-start-1 lg:row-start-1">
          <p id="hero-heading" className="text-meta font-bold text-muted">
            APIで使うと月いくら？
            <span className="block font-normal">アプリやサービスに組み込んで使うときの料金です</span>
          </p>
          {cheapest ? (
            <>
              <p className="mt-4 text-[16px] font-bold text-ink">いちばん安いモデルなら</p>
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1 leading-none" aria-live="polite">
                <span className="text-[22px] font-bold text-ink">月</span>
                {yenNumber(cheapest.jpy!).startsWith("約") && <span className="text-[22px] font-bold text-signal">約</span>}
                {/* 要の数字だけ赤。読み込み時と使い方を変えたときに数え上げる */}
                <CountUp value={cheapest.jpy!} className="text-[3.6rem] font-black tracking-tight tabular-nums text-signal sm:text-[4.75rem]" />
                <span className="text-[26px] font-bold text-ink">円</span>
                <span className="ml-1 text-[18px] font-bold text-ink-soft">から</span>
              </p>
              <p className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-[15px] text-ink">
                <Chip row={cheapest.row} />
                <strong className="font-bold">{cheapest.row.product}</strong>
                <span className="text-muted">{cheapest.row.companyName}</span>
              </p>
              {tokens.actions > 0 && (
                <p className="mt-1.5 text-[14px] text-ink-soft">
                  1回あたり <strong className="font-bold tabular-nums text-ink">{yen(cheapest.jpy! / tokens.actions)}</strong>
                  <span className="text-muted">（質問・要約・記事・画像の合計 月{tokens.actions.toLocaleString()}回）</span>
                </p>
              )}
            </>
          ) : (
            <p className="mt-4 text-[15px] text-ink-soft">条件に合うモデルがありません。</p>
          )}
        </div>

        <div className="min-w-0 lg:col-start-2 lg:row-span-2 lg:row-start-1 lg:border-l lg:border-line lg:pl-12">
          <UsagePanel
            usage={usage}
            presetKey={preset?.key}
            custom={custom}
            tokens={fromUsage}
            rate={rate}
            onUsage={(next) => {
              setCustom(null);
              setUsage(next);
            }}
            onCustom={setCustom}
            onRate={setRate}
          />
        </div>

        <div className="min-w-0 border-t border-line pt-5 lg:col-start-1 lg:row-start-2">
          {cheapest && priciest && priciest !== cheapest && (
            <p className="text-note leading-[1.8] text-ink-soft">
              同じ使い方でも、いちばん高い{priciest.row.product}だと月
              <strong className="mx-0.5 font-bold tabular-nums text-ink">{yen(priciest.jpy!)}</strong>
              （約{Math.round(priciest.jpy! / cheapest.jpy!).toLocaleString()}倍）です。
            </p>
          )}
          {cheapestPlan && (
            <p className="mt-2 text-note leading-[1.8] text-ink-soft">
              ChatGPTやClaudeをアプリで使うなら、
              <a href="#plan-heading" className="font-bold text-ink underline underline-offset-[3px]">
                月額プラン
              </a>
              （月 {yen(planYen(cheapestPlan, rate))}〜）を選ぶ方法もあります。
            </p>
          )}
          <p className="mt-3 text-meta text-muted">{usageLabel}・1ドル＝{rate}円で計算</p>
        </div>
      </section>

      {/* ============ モデルごとの月額（棒グラフ） ============ */}
      <section aria-labelledby="rank-heading" className="mt-16">
        <SectionHead id="rank-heading" note={`${usageLabel}。棒が短いほど安い。`}>
          同じ使い方だと、モデルごとに月いくら？
        </SectionHead>
        <label className="mb-4 inline-flex items-center gap-2 text-note text-ink-soft">
          <input type="checkbox" checked={japanOnly} onChange={(e) => setJapanOnly(e.target.checked)} className="size-4 accent-[var(--accent)]" />
          日本で使えるものだけ
        </label>
        <ol className="grid">
          {ranked.map(({ row, jpy }, index) => {
            const best = cheapest?.row.id === row.id;
            return (
              <li
                key={row.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1.5 border-b border-line py-3 sm:grid-cols-[16.5rem_minmax(0,1fr)_7rem]"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <span className="w-5 shrink-0 text-right text-meta tabular-nums text-muted">{jpy === null ? "" : index + 1}</span>
                  <Chip row={row} />
                  <span className="truncate text-[14.5px] font-bold text-ink">{row.product}</span>
                  {best && <span className="shrink-0 rounded-sm bg-signal px-1.5 py-[1px] text-badge font-bold text-signal-ink">最安</span>}
                </span>
                <span className={`text-right tabular-nums sm:order-last ${best ? "text-[15px] font-black text-signal" : "text-[14px] font-bold text-ink"}`}>
                  {jpy === null ? <span className="text-meta font-normal text-muted">計算できません</span> : yen(jpy)}
                </span>
                <span className="col-span-2 sm:col-span-1">
                  {jpy === null ? (
                    <span className="text-meta text-muted">{row.input === null ? "入力" : "出力"}の料金がまだ記事で確認できていません</span>
                  ) : (
                    <span className="block h-2.5 overflow-hidden rounded-[2px] bg-surface" aria-hidden>
                      {/* 最安だけ赤、ほかは紺。読み込み時に順に伸びる */}
                      <span
                        className={`bar-grow block h-full rounded-[2px] ${best ? "bg-signal" : "bg-accent"}`}
                        style={{
                          width: `${maxJpy > 0 ? Math.max(1.5, (jpy / maxJpy) * 100) : 0}%`,
                          ["--i" as string]: index,
                        }}
                      />
                    </span>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
        {ranked.length === 0 && <p className="py-8 text-center text-[13px] text-muted">条件に合うモデルがありません。</p>}
      </section>

      {/* ============ 料金と日本対応の一覧 ============ */}
      <section aria-labelledby="api-heading" className="mt-16">
        <SectionHead id="api-heading" note="料金は100万トークンあたりの米ドル（各社のAPIの表記どおり）。月の目安は上の使い方の場合です。">
          モデルごとの料金と、日本で使えるか
        </SectionHead>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-[13px]">
          <label className="flex items-center gap-2">
            <span className="text-ink-soft">並び替え</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              className="rounded-md border border-line-strong bg-paper px-2 py-1 text-[13px] text-ink"
            >
              <option value="cost">安い順</option>
              <option value="company">会社ごと</option>
              <option value="newest">記事が新しい順</option>
            </select>
          </label>
          {legacyCount > 0 && (
            <label className="flex items-center gap-2 text-ink-soft">
              <input type="checkbox" checked={showLegacy} onChange={(e) => setShowLegacy(e.target.checked)} className="size-4 accent-[var(--accent)]" />
              以前のモデルも表示（{legacyCount}件）
            </label>
          )}
        </div>

        <div
          aria-hidden
          className="hidden grid-cols-[minmax(0,1.4fr)_6.5rem_5.5rem_6.5rem_minmax(0,1.2fr)] gap-4 border-b border-ink pb-2 text-meta font-bold text-muted md:grid"
        >
          <span>モデル</span>
          <span className="text-right">入力</span>
          <span className="text-right">出力</span>
          <span className="text-right">月の目安</span>
          <span>日本・無料枠</span>
        </div>
        <ul>
          {listed.map(({ row, jpy }, index) => {
            const groupStart = sort === "company" && (index === 0 || listed[index - 1].row.companySlug !== row.companySlug);
            return (
              <li key={row.id}>
                {groupStart && (
                  <p className="mt-5 flex items-center gap-2 pb-1 text-[13px] font-bold text-ink">
                    <Chip row={row} />
                    {row.companyName}
                  </p>
                )}
                <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-2 border-b border-line py-3.5 md:grid-cols-[minmax(0,1.4fr)_6.5rem_5.5rem_6.5rem_minmax(0,1.2fr)] md:items-center">
                  <div className="min-w-0">
                    <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      {sort !== "company" && <Chip row={row} />}
                      <ProductName row={row} />
                      {row.legacy && <span className="rounded-sm border border-line-strong px-1.5 text-badge text-muted">以前のモデル</span>}
                    </p>
                    <p className="mt-0.5 text-meta text-muted">
                      {sort !== "company" && <>{row.companyName} · </>}
                      <SourceLink row={row} />
                    </p>
                  </div>

                  {/* スマホ: 料金を右にまとめる */}
                  <div className="text-right text-[13px] tabular-nums text-ink md:hidden">
                    <p>
                      <span className="text-meta text-muted">入力 </span>
                      <Usd value={row.input} />
                    </p>
                    <p>
                      <span className="text-meta text-muted">出力 </span>
                      <Usd value={row.output} />
                    </p>
                    {jpy !== null && <p className="mt-0.5 font-bold">月{yen(jpy)}</p>}
                  </div>

                  <p className="hidden text-right text-[14px] tabular-nums text-ink md:block">
                    <Usd value={row.input} />
                    {row.cachedInput !== null && (
                      <span className="block whitespace-nowrap text-meta text-muted">キャッシュ {formatMoney(row.cachedInput, "USD")}</span>
                    )}
                  </p>
                  <p className="hidden text-right text-[14px] tabular-nums text-ink md:block">
                    <Usd value={row.output} />
                  </p>
                  <p className="hidden text-right text-[14px] font-bold tabular-nums text-ink md:block">
                    {jpy === null ? <span className="font-normal text-muted">—</span> : yen(jpy)}
                  </p>

                  <div className="col-span-2 flex flex-wrap items-center gap-1.5 md:col-span-1">
                    <JapanCue japan={row.japan} status={row.status} />
                    <FreeCue free={row.free} kind="api" />
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      {/* ============ 月額プラン ============ */}
      <section aria-labelledby="plan-heading" className="mt-16">
        <SectionHead id="plan-heading" note="ChatGPT・Claude・Geminiなどをアプリで使うときの有料プラン。記事で金額を確認できたものだけ載せています。">
          アプリで使うなら、月額プランはいくら？
        </SectionHead>
        <ul className="grid gap-3 sm:grid-cols-2">
          {plans.map((row) => (
            <li key={row.id} className="rounded-xl border border-line p-4 sm:p-5">
              <p className="flex items-center gap-2 text-[13px] text-muted">
                <Chip row={row} />
                {row.companyName}
              </p>
              <p className="mt-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="text-[18px] font-bold text-ink">
                  {row.product} {row.plan}
                </span>
                <span className="tabular-nums">
                  <span className="text-[13px] font-bold text-ink">月 </span>
                  <span className="text-[24px] font-black text-ink">
                    {yenNumber(planYen(row, rate))}
                  </span>
                  <span className="text-[13px] font-bold text-ink">円</span>
                  {row.currency === "USD" && <span className="ml-1.5 text-meta text-muted">（{formatMoney(row.monthly ?? 0, "USD")}）</span>}
                </span>
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                <JapanCue japan={row.japan} status={row.status} />
                <FreeCue free={row.free} kind="plan" />
              </div>
              <p className="mt-3 text-meta text-muted">
                <SourceLink row={row} />
              </p>
            </li>
          ))}
        </ul>
        {plans.length === 0 && <p className="py-6 text-center text-[13px] text-muted">条件に合うプランがありません。</p>}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 使い方の入力                                                         */
/* ------------------------------------------------------------------ */

function UsagePanel({
  usage,
  presetKey,
  custom,
  tokens,
  rate,
  onUsage,
  onCustom,
  onRate,
}: {
  usage: Usage;
  presetKey?: string;
  custom: { input: number; output: number } | null;
  tokens: { input: number; output: number };
  rate: number;
  onUsage: (usage: Usage) => void;
  onCustom: (value: { input: number; output: number } | null) => void;
  onRate: (value: number) => void;
}) {
  const man = (n: number) => Math.round(n / 1000) / 10;
  return (
    <div>
      <p className="text-[15px] font-bold text-ink">どれくらい使う？</p>
      <div role="group" aria-label="使い方の例" className="mt-3 grid grid-cols-3 overflow-hidden rounded-lg border border-line-strong text-[13px] font-bold">
        {PRESETS.map((p, i) => {
          const on = presetKey === p.key;
          return (
            <button
              key={p.key}
              type="button"
              aria-pressed={on}
              onClick={() => onUsage(p.usage)}
              className={`px-2 py-2 transition-colors ${i > 0 ? "border-l border-line-strong" : ""} ${on ? "bg-ink text-paper" : "text-ink-soft hover:bg-surface"}`}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <ul className={`mt-4 grid gap-3.5 ${custom ? "opacity-50" : ""}`}>
        {ACTIVITIES.map((a) => (
          <li key={a.key} className="flex items-center justify-between gap-3">
            <span className="min-w-0">
              <span className="block text-[14px] font-bold text-ink">{a.label}</span>
              <span className="block text-note leading-snug text-muted">
                <span className="font-bold text-ink-soft">{a.per === "day" ? "1日あたり" : "1か月あたり"}</span>・{a.note}
              </span>
            </span>
            <Stepper label={a.label} unit={a.unit} value={usage[a.key]} onChange={(value) => onUsage({ ...usage, [a.key]: value })} />
          </li>
        ))}
      </ul>
      <p className="mt-4 text-note leading-[1.7] text-muted">
        トークンは、AIが文章を数える単位です。日本語はおよそ1文字＝1トークン前後。1日の回数は30日分で計算しています。
      </p>

      <details className="mt-3 rounded-lg border border-line px-3.5 py-2.5" open={custom !== null}>
        <summary className="cursor-pointer text-[13px] font-bold text-ink-soft">詳しく設定（トークン数・為替）</summary>
        <div className="mt-3 grid grid-cols-3 gap-2.5">
          <NumberField
            label="入力"
            unit="万トークン/月"
            value={custom ? custom.input : man(tokens.input)}
            onChange={(value) => onCustom({ input: value, output: custom ? custom.output : man(tokens.output) })}
          />
          <NumberField
            label="出力"
            unit="万トークン/月"
            value={custom ? custom.output : man(tokens.output)}
            onChange={(value) => onCustom({ input: custom ? custom.input : man(tokens.input), output: value })}
          />
          <NumberField label="1ドル" unit="円" value={rate} onChange={onRate} />
        </div>
        <p className="mt-2 text-note leading-[1.7] text-muted">
          入力＝AIに渡す文章、出力＝AIが書く文章。ここを変えると、上の使い方ではなくこの数で計算します。
        </p>
        {custom && (
          <button type="button" onClick={() => onCustom(null)} className="mt-2 text-meta font-bold text-ink underline underline-offset-2">
            使い方から計算に戻す
          </button>
        )}
      </details>
    </div>
  );
}

function Stepper({
  label,
  unit,
  value,
  onChange,
}: {
  label: string;
  unit: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const step = value >= 20 ? 5 : 1;
  const button =
    "grid size-8 place-items-center rounded-md border border-line-strong text-[16px] font-bold text-ink hover:bg-surface disabled:opacity-30";
  return (
    <span className="flex shrink-0 items-center gap-1">
      <button type="button" aria-label={`${label}を減らす`} className={button} disabled={value <= 0} onClick={() => onChange(Math.max(0, value - step))}>
        −
      </button>
      <span className="w-12 text-center text-[16px] font-bold tabular-nums text-ink" aria-live="polite">
        {value}
        <span className="text-meta font-normal text-muted">{unit}</span>
      </span>
      <button type="button" aria-label={`${label}を増やす`} className={button} onClick={() => onChange(Math.min(500, value + step))}>
        ＋
      </button>
    </span>
  );
}

function NumberField({ label, unit, value, onChange }: { label: string; unit: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="grid min-w-0 gap-1">
      <span className="text-meta font-bold text-ink-soft">
        {label}
        <span className="block font-normal text-muted">{unit}</span>
      </span>
      <input
        type="number"
        inputMode="decimal"
        min={0}
        value={Number.isFinite(value) ? value : 0}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
        className="w-full min-w-0 rounded-md border border-line-strong bg-paper px-2 py-1.5 text-right text-[14px] tabular-nums text-ink"
      />
    </label>
  );
}

/* ------------------------------------------------------------------ */
/* 小さな部品                                                           */
/* ------------------------------------------------------------------ */

function SectionHead({ id, children, note }: { id: string; children: React.ReactNode; note?: string }) {
  return (
    <div className="mb-5">
      <h2 id={id} className="text-balance text-[1.4rem] font-extrabold leading-snug text-ink sm:text-[1.65rem]">
        {children}
      </h2>
      {note && <p className="text-note mt-1.5 leading-relaxed text-muted">{note}</p>}
    </div>
  );
}

/** 会社の目印。企業の色は小さな四角だけに使い、文字は載せない（白い文字が企業の色の上で読めないため） */
function Chip({ row }: { row: PriceRow }) {
  return <span aria-hidden data-decorative="" className="inline-block size-2.5 shrink-0 rounded-[2px] ring-1 ring-line-strong" style={{ background: row.color }} />;
}

/** 数字を数え上げて表示する（動きを減らす設定では、すぐ最終の値） */
function CountUp({ value, className }: { value: number; className?: string }) {
  const [shown, setShown] = useState(value);
  const from = useRef(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      from.current = value;
      setShown(value);
      return;
    }
    const start = from.current;
    const t0 = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / 900);
      const v = start + (value - start) * (1 - (1 - p) ** 3);
      from.current = v;
      setShown(v);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <span className={className}>{yenNumber(shown).replace(/^約/, "")}</span>;
}

function ProductName({ row }: { row: PriceRow }) {
  const className = "text-[15px] font-bold leading-snug text-ink";
  if (!row.productSlug) return <span className={className}>{row.product}</span>;
  return (
    <Link href={`/products/${row.productSlug}`} className={`${className} underline decoration-transparent underline-offset-[3px] hover:decoration-ink`}>
      {row.product}
    </Link>
  );
}

function Usd({ value }: { value: number | null }) {
  if (value === null) return <span className="text-muted">—</span>;
  return <>{formatMoney(value, "USD")}</>;
}

/** 出典の記事（いつの記事で確かめた数字か） */
function SourceLink({ row }: { row: PriceRow }) {
  return (
    <>
      <Link
        href={`/articles/${row.source.slug}`}
        title={`記事の記載:「${row.source.quote}」`}
        className="underline decoration-line-strong underline-offset-2 hover:text-ink"
      >
        {day.format(Date.parse(row.source.publishedAt))}の記事で確認
      </Link>
      {row.confirmedIn > 1 && <span>（{row.confirmedIn}本で一致）</span>}
    </>
  );
}

/* 日本対応は意味の色（緑＝使える・琥珀＝条件付き・灰＝未確認）だけを使う */
const JAPAN: Record<PriceRow["japan"], { label: string; className: string; icon: "ok" | "partial" | "no" | "unknown" }> = {
  ok: { label: "日本で使える", className: "bg-go-soft text-go", icon: "ok" },
  partial: { label: "日本は条件付き", className: "border border-official/50 text-official", icon: "partial" },
  no: { label: "日本は未提供", className: "bg-surface text-ink-soft", icon: "no" },
  unknown: { label: "日本対応は未確認", className: "bg-surface text-muted", icon: "unknown" },
};

function JapanCue({ japan, status }: { japan: PriceRow["japan"]; status: PriceRow["status"] }) {
  const pre = status === "soon" || status === "demo";
  const item = pre ? { label: "提供前", className: "bg-surface text-muted", icon: "unknown" as const } : JAPAN[japan];
  return (
    <span className={`inline-flex items-center gap-1 rounded-sm px-1.5 py-[2px] text-badge font-bold ${item.className}`}>
      <CueIcon kind={item.icon} />
      {item.label}
    </span>
  );
}

function FreeCue({ free, kind }: { free: boolean | null; kind: "api" | "plan" }) {
  if (free === true) {
    return (
      <span className="inline-flex items-center rounded-sm border border-line-strong px-1.5 py-[1px] text-badge font-bold text-ink-soft">
        {kind === "plan" ? "無料プランあり" : "無料で試せる"}
      </span>
    );
  }
  if (free === false) return <span className="text-meta text-muted">有料のみ</span>;
  return null;
}

function CueIcon({ kind }: { kind: "ok" | "partial" | "no" | "unknown" }) {
  const common = { width: 11, height: 11, viewBox: "0 0 12 12", fill: "none", stroke: "currentColor", strokeWidth: 2, "aria-hidden": true } as const;
  if (kind === "ok") return <svg {...common}><path d="M2 6.5 5 9.5 10 3" strokeLinecap="round" strokeLinejoin="round" /></svg>;
  if (kind === "partial") return <svg {...common}><path d="M6 2 10.5 10h-9Z" strokeLinejoin="round" /></svg>;
  if (kind === "no") return <svg {...common}><path d="M3 3l6 6M9 3 3 9" strokeLinecap="round" /></svg>;
  return <svg {...common}><circle cx="6" cy="6" r="4.5" /></svg>;
}
