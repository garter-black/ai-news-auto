"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import RelTime from "@/components/RelTime";
import { PushBell } from "@/components/PushOptIn";
import { liveLabel } from "@/lib/live-line";

type Item = { slug: string; title: string; publishedAt: string; fast?: boolean };

/**
 * ヘッダー下の1行。トップでは先頭の大きな記事と重ならないよう2番目を出す。右端に通知の鈴。
 * 札は本当の速報だけ赤い「速報」、ほかは紺の「最新」（lib/live-line.ts。台帳 W23）。
 * ページは静的に作って長くキャッシュするので、最初は描いた時刻（renderedAt）で決め、表示後に閲覧時刻で当て直す
 * （サーバーとブラウザで違う札を描くとハイドレーションの不一致になるため）。
 */
export default function LiveLine({ items, renderedAt }: { items: [Item, Item] | Item[]; renderedAt: number }) {
  const path = usePathname() ?? "/";
  const item = path === "/" ? (items[1] ?? items[0]) : items[0];
  const [now, setNow] = useState(renderedAt);
  useEffect(() => setNow(Date.now()), []);
  if (!item) return null;
  const label = liveLabel(item, now);
  return (
    <div className="live-line">
      <div className="mx-auto flex h-9 max-w-[1240px] items-center gap-2.5 px-4 text-meta sm:px-8">
        <span
          data-live-label={label}
          className={`shrink-0 rounded-sm px-1.5 py-[1px] text-badge font-bold tracking-[0.06em] ${label === "速報" ? "bg-live text-live-ink" : "bg-accent text-accent-ink"}`}
        >
          {label}
        </span>
        <RelTime iso={item.publishedAt} className="index-num shrink-0 text-muted" />
        <Link
          href={`/articles/${item.slug}`}
          className="min-w-0 truncate font-bold text-ink underline-offset-[3px] hover:underline"
        >
          {item.title}
        </Link>
        {/* 速報のプッシュ通知（押すと説明の吹き出し → 登録） */}
        <PushBell />
      </div>
    </div>
  );
}
