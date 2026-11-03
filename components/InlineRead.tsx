import { articleTitle } from "@/lib/headline";
import Link from "next/link";
import type { NextRead } from "@/lib/articles";

/** 本文の途中に1行だけ差し込む「あわせて読みたい」。読み進める流れを止めない控えめな見た目にする */
export default function InlineRead({ item }: { item?: NextRead }) {
  if (!item) return null;
  return (
    <Link
      href={`/articles/${item.article.slug}`}
      className="group my-8 flex max-w-[42em] items-center gap-3 border-y border-line py-3.5"
    >
      <span className="shrink-0 text-meta font-bold text-live">あわせて読みたい</span>
      <span className="min-w-0 flex-1 truncate text-note font-bold text-ink group-hover:underline">
        {articleTitle(item.article)}
      </span>
      <span aria-hidden className="text-muted">→</span>
    </Link>
  );
}
