import type { ReactNode } from "react";
import { CALC_SLOT } from "@/lib/charts";

/**
 * 記事本文の HTML。計算機の置き場所の印（lib/charts.ts の CALC_SLOT）があれば、そこで本文を分けて計算機を挟む。
 * 印が無ければ今までどおり1つの <article> を出す。
 */
export default function ArticleProse({ html, className, slot }: { html: string; className: string; slot?: ReactNode }) {
  const at = slot ? html.indexOf(CALC_SLOT) : -1;
  if (at < 0) return <article className={className} dangerouslySetInnerHTML={{ __html: html.replace(CALC_SLOT, "") }} />;
  const before = html.slice(0, at);
  const after = html.slice(at + CALC_SLOT.length);
  return (
    <>
      <article className={className} dangerouslySetInnerHTML={{ __html: before }} />
      {slot}
      {after.trim() && <article className={className.replace(/\bmt-\d+\b/, "")} dangerouslySetInnerHTML={{ __html: after }} />}
    </>
  );
}
