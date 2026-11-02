/**
 * 計算機（/tools）と記事の図で使う計算。サーバー・ブラウザ共通、依存なし。
 * ここの式は tests/test_charts_calc.cjs で確かめている。
 */

/** 日本の消費税（標準税率） */
export const CONSUMPTION_TAX = 0.1;

export type YenBreakdown = {
  /** 税抜きの円 */
  base: number;
  /** 消費税 */
  tax: number;
  /** カード会社の海外事務手数料 */
  fee: number;
  /** 合計 */
  total: number;
};

/**
 * ドル → 円。消費税はドルの価格に10%がかかる場合の目安（海外のサービスは請求のときに足されることが多い）。
 * 手数料は「税込みの請求額」にかかるものとして計算する（カード会社の一般的な扱い）。端数は1円単位で四捨五入。
 */
export function usdToYen(usd: number, rate: number, withTax: boolean, feePercent = 0): YenBreakdown {
  if (!Number.isFinite(usd) || !Number.isFinite(rate) || usd < 0 || rate <= 0) return { base: 0, tax: 0, fee: 0, total: 0 };
  const base = usd * rate;
  const tax = withTax ? base * CONSUMPTION_TAX : 0;
  const fee = feePercent > 0 ? (base + tax) * (feePercent / 100) : 0;
  return { base: Math.round(base), tax: Math.round(tax), fee: Math.round(fee), total: Math.round(base + tax + fee) };
}

export type BillingCompare = {
  /** 月払いで months か月使ったときの合計 */
  monthlyTotal: number;
  /** 年払いの一括額（年払いが無ければ null） */
  annualTotal: number | null;
  /** 何か月以上使うなら年払いの方が安いか（年払いが無ければ null） */
  breakEvenMonths: number | null;
  /** 12か月使ったときの差（月払い − 年払い） */
  yearlySaving: number | null;
};

export function compareBilling(monthly: number, annualTotal: number | null, months: number): BillingCompare {
  const m = Math.max(1, Math.min(12, Math.round(months)));
  const monthlyTotal = monthly * m;
  if (annualTotal === null || !(monthly > 0)) return { monthlyTotal, annualTotal: null, breakEvenMonths: null, yearlySaving: null };
  return {
    monthlyTotal,
    annualTotal,
    breakEvenMonths: Math.ceil(annualTotal / monthly - 1e-9),
    yearlySaving: monthly * 12 - annualTotal,
  };
}

/** 「Pro何個分」の値段か（小数1けた）。比べる相手が無料・不明なら null */
export function timesOf(price: number, reference: number): number | null {
  if (!(reference > 0) || !(price >= 0)) return null;
  return Math.round((price / reference) * 10) / 10;
}

/** 公式の説明にある「Pro の 10 倍の使用量」の 10（無ければ null） */
export function usageMultiple(limits: string[], referenceName: string): number | null {
  const name = referenceName.normalize("NFKC").replace(/\s+/g, "").replace(/[.*+?^$()|[\]\\{}]/g, "\\$&");
  const pattern = new RegExp(name + "の(\\d+(?:\\.\\d+)?)倍の(?:毎週の)?使用量");
  for (const text of limits) {
    const m = text.normalize("NFKC").replace(/\s+/g, "").match(pattern);
    if (m) return Number(m[1]);
  }
  return null;
}

/**
 * 防衛特別所得税・復興特別所得税（国の税）の税率。
 * 出典: 財務省「令和8年度税制改正の大綱」（防衛特別所得税＝基準所得税額×1%、令和9年＝2027年分から当分の間。
 * 復興特別所得税は2027年分から1.1%に下げ、課税期間を2047年まで延長）。国税庁「令和9年分 源泉徴収税額表」。
 */
export function surtaxRates(year: number): { defense: number; reconstruction: number } {
  if (year < 2013) return { defense: 0, reconstruction: 0 };
  if (year <= 2026) return { defense: 0, reconstruction: 0.021 };
  if (year <= 2047) return { defense: 0.01, reconstruction: 0.011 };
  return { defense: 0.01, reconstruction: 0 };
}

/** 改正前のきまり（復興特別所得税 2.1% を 2037年まで）。改正でどう変わったかを並べるため */
export function surtaxRatesBeforeReform(year: number): { defense: number; reconstruction: number } {
  if (year < 2013 || year > 2037) return { defense: 0, reconstruction: 0 };
  return { defense: 0, reconstruction: 0.021 };
}

export type Surtax = { defense: number; reconstruction: number; total: number };

/** 基準所得税額から、その年の防衛特別所得税と復興特別所得税（それぞれ1円未満切り捨て） */
export function surtax(baseIncomeTax: number, year: number, beforeReform = false): Surtax {
  const base = Math.max(0, Math.floor(baseIncomeTax));
  const rates = beforeReform ? surtaxRatesBeforeReform(year) : surtaxRates(year);
  const defense = Math.floor(base * rates.defense);
  const reconstruction = Math.floor(base * rates.reconstruction);
  return { defense, reconstruction, total: defense + reconstruction };
}

/**
 * 源泉徴収票の「源泉徴収税額」（所得税と復興特別所得税などの合計。2026年分までも2027年分からも合計の率は 2.1%）から、
 * 基準所得税額（所得税だけ）の目安。年末調整で100円未満を切り捨てているので、数十円ずれることがある。
 */
export function baseFromWithheld(withheld: number): number {
  if (!(withheld > 0)) return 0;
  return Math.floor(withheld / 1.021);
}

/** 1,234,567 → "123.5万"、2.5e8 → "2.5億"（図の差分の表示用） */
export function compactJa(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  const trim = (n: number) => (Math.round(n * 10) / 10).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
  if (abs >= 1e12) return `${sign}${trim(abs / 1e12)}兆`;
  if (abs >= 1e8) return `${sign}${trim(abs / 1e8)}億`;
  if (abs >= 1e4) return `${sign}${trim(abs / 1e4)}万`;
  return `${sign}${abs.toLocaleString("ja-JP", { maximumFractionDigits: 2 })}`;
}

export function yen(value: number): string {
  return `${Math.round(value).toLocaleString("ja-JP")}円`;
}
