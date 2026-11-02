import Link from "next/link";
import type { ReadNextLink } from "@/lib/read-next";

const KIND_LABEL: Record<ReadNextLink["kind"], string> = {
  choose: "選び方",
  compare: "比較",
  guide: "使い方",
};

/**
 * 記事に合う広告が無いときの「あわせて読みたい」。自前のガイド・比較ページへの案内で、広告ではない（PR表記なし）。
 * 見た目は本文中の選び方ガイドの囲み（ChooseGuideNote）とそろえる。
 */
export default function ReadNextBox({ link, className = "" }: { link: ReadNextLink; className?: string }) {
  return (
    <aside data-funnel-pos="article" data-read-next={link.kind} aria-label="あわせて読みたいガイド" className={`max-w-[40em] border-l-[3px] border-accent bg-surface px-4 py-3.5 ${className}`}>
      <p className="flex items-center gap-2 text-meta font-bold text-muted">
        あわせて読みたい
        <span className="rounded-sm border border-line-strong px-1.5 text-badge font-bold text-ink-soft">{KIND_LABEL[link.kind]}</span>
      </p>
      <Link href={link.href} className="mt-1 block text-[16px] font-bold leading-snug text-ink hover:text-accent hover:underline">
        {link.label}
      </Link>
      {link.note && <p className="mt-1 line-clamp-2 text-note leading-[1.7] text-ink-soft">{link.note}</p>}
    </aside>
  );
}
