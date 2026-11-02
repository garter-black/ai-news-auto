import type { ImageCredit } from "@/lib/articles";

/**
 * 公式発表の写真（scripts/images.py が縮小して自前で配信）の出典の文。記事ページ・トップの写真の下・SNS カバーで同じ文を使う。
 * 「画像：Sakana AI（公式発表より）」「画像：株式会社〇〇（PR TIMES配信の公式発表より）」
 */
export function officialCreditText(credit?: Pick<ImageCredit, "kind" | "org" | "via" | "provider">): string | null {
  if (credit?.kind !== "official") return null;
  const org = credit.org || credit.via || credit.provider;
  if (!org) return null;
  if (credit.via && org !== credit.via) return `画像：${org}（${credit.via}配信の公式発表より）`;
  if (credit.via) return `画像：${credit.via}配信の公式発表より`;
  return `画像：${org}（公式発表より）`;
}
