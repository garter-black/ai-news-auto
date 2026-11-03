import type { ReactNode } from "react";
import { Phrases } from "@/lib/highlight";

/** 運営者情報・規約系ページの共通レイアウト */
export default function LegalPage({
  eyebrow,
  title,
  lead,
  updatedAt,
  children,
}: {
  eyebrow: string;
  title: string;
  lead?: string;
  updatedAt: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-3xl px-5 py-14 sm:px-8">
      <p className="eyebrow">{eyebrow}</p>
      <h1 className="t-h1 mt-2"><Phrases text={title} /></h1>
      {lead && (
        <p className="mt-5 border-l-[3px] border-accent pl-4 text-desc leading-[1.9] text-ink-soft">
          {lead}
        </p>
      )}
      <p className="index-num mt-6 text-meta text-muted">
        最終更新: {updatedAt}
      </p>

      <div className="prose mt-10 max-w-none">{children}</div>
    </div>
  );
}
