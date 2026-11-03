/**
 * 「記載なし」を読者に見せない（2026-11-03 オーナーのルール W11）。
 * 料金表・比較表・製品の基本情報・選び方の表に入っている「公式ページに記載なし」「税の扱いの記載なし」などの文を、表示の前に取り除く。
 * 文の一部なら、その文（か、かっこ書き）だけを消す。全部が「記載なし」なら空文字（表のセルは空欄・箇条書きは項目ごと消す）。
 * 見張り: scripts/ui_check.mjs（「記載なし」の表示）・tests/test_no_data.cjs
 */
const NO_DATA_RE = /記載(?:は|が)?(?:なし|ありません|されていません)|言及(?:は|が)?(?:なし|ありません)/;
const NO_DATA_PAREN_RE = /[（(][^（）()]*(?:記載(?:は|が)?(?:なし|ありません)|言及(?:は|が)?(?:なし|ありません))[^（）()]*[）)]/g;

export function withoutNoData(text: string | null | undefined): string {
  const t = (text ?? "").replace(NO_DATA_PAREN_RE, "").trim();
  if (!NO_DATA_RE.test(t)) return t;
  const sentences = t.match(/[^。]+。?/g) ?? [];
  return sentences
    .filter((s) => !NO_DATA_RE.test(s))
    .join("")
    .trim();
}

/** 箇条書き: 「記載なし」だけの項目は消し、文の一部なら、その文だけ消す */
export function withoutNoDataList(items: readonly string[] | null | undefined): string[] {
  return (items ?? []).map(withoutNoData).filter(Boolean);
}

/** 表のセルに出す値があるか（空・「—」・「記載なし」だけのセルは値なし）。値の無い列は列ごと隠す（components/PriceTable.tsx） */
export function hasValue(text: string | null | undefined): boolean {
  const t = withoutNoData(text);
  return Boolean(t) && !/^[—―\-–ー・]+$/.test(t);
}
