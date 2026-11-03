import stored from "@/content/pricing/_fx.json";

/**
 * サイト全体で使うドル円の為替（1か所だけ。2026-11-03 オーナー決定・台帳 W21）。
 *
 * 元は content/pricing/_fx.json。scripts/pricing_watch.py（pricing_watch.yml、毎朝）が ECB（欧州中央銀行）の参照レートから
 * 1日1回だけ書き、記事を作る側（scripts/pricing.py の load_fx → scripts/facts_bundle.py）も同じファイルを読む。
 * AI料金表・料金の段の表・記事の図や計算機・トップの案内は、すべてここから読む（ページごとに別の数字を持たない）。
 * 前は AI料金表だけ 1ドル＝150円の決め打ちで、ほかのページ（157.9円）と食い違っていた。
 */
export type Fx = { rate: number; date: string; source: string; sourceUrl?: string; fallback: boolean };

/** _fx.json が読めないときの控え（2026-11-02 の ECB 参照レート。1ユーロ=1.1216ドル=177.10円から計算） */
export const FX_FALLBACK: Fx = {
  rate: 157.9,
  date: "2026-11-02",
  source: "ECB参照レート（ユーロ経由で計算）",
  sourceUrl: "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml",
  fallback: true,
};

const raw = stored as { rate?: unknown; date?: unknown; source?: unknown; sourceUrl?: unknown };

const FX: Fx =
  typeof raw.rate === "number" && raw.rate > 50 && raw.rate < 400 && typeof raw.date === "string" && raw.date
    ? {
        rate: raw.rate,
        date: raw.date,
        source: typeof raw.source === "string" && raw.source ? raw.source : "ECB参照レート",
        sourceUrl: typeof raw.sourceUrl === "string" ? raw.sourceUrl : undefined,
        fallback: false,
      }
    : FX_FALLBACK;

/** サイト共通のドル円 */
export function getUsdJpy(): Fx {
  return FX;
}

/** 「1ドル＝157.90円（2026/11/02・ECB参照レート（ユーロ経由で計算））」 */
export function fxLabel(fx: Fx = FX): string {
  return `1ドル＝${fx.rate.toFixed(2)}円（${fx.date.replace(/-/g, "/")}・${fx.source}）`;
}
