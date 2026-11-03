/**
 * 計算機（/tools/defense-tax）と記事の図で使う計算。サーバー・ブラウザ共通、依存なし。
 * ここの式は tests/test_charts_calc.cjs で確かめている。
 */

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
