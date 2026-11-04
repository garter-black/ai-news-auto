"use client";

import { useEffect, useState } from "react";
import type { OfferItem } from "@/lib/offers";
import { offerIsActive, nextOfferExpiry } from "@/lib/offer-time";
import { catalogPriceText } from "./parts";

const STORAGE_KEY = "saisoku:pr-bar-closed";

/**
 * スマホ用：記事を読み進めると画面下に「イチオシ」を1つ出す。
 * 本文中のおすすめ枠が見えている間は重ねないよう隠し、閉じたらそのタブでは出さない。
 */
export default function MobilePrBar({ item, watchId }: { item: OfferItem; watchId: string }) {
  const [scrolled, setScrolled] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [closed, setClosed] = useState(true);
  const [active, setActive] = useState(!item.activeUntil);
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    let timer: number | undefined;
    const check = () => {
      if (timer) window.clearTimeout(timer);
      const current = Date.now();
      setNow(current);
      setActive(offerIsActive(item.activeUntil, current));
      const next = nextOfferExpiry([item.activeUntil, item.priceFreshUntil], current);
      if (next) timer = window.setTimeout(check, Math.min(next - current + 50, 2_147_483_647));
    };
    check();
    document.addEventListener("visibilitychange", check);
    return () => { if (timer) window.clearTimeout(timer); document.removeEventListener("visibilitychange", check); };
  }, [item.activeUntil, item.priceFreshUntil]);

  useEffect(() => {
    try {
      setClosed(sessionStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      setClosed(false);
    }
    const onScroll = () => setScrolled(window.scrollY > 700);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    const targets = [document.getElementById(watchId), document.querySelector('[data-aff-pos="ranking-rail"]')]
      .filter((element): element is Element => element !== null);
    const intersecting = new Set<Element>();
    const observer = targets.length
      ? new IntersectionObserver((entries) => {
          for (const entry of entries) {
            if (entry.isIntersecting) intersecting.add(entry.target);
            else intersecting.delete(entry.target);
          }
          setBlocked(intersecting.size > 0);
        }, { threshold: 0 })
      : undefined;
    for (const target of targets) observer?.observe(target);
    return () => {
      window.removeEventListener("scroll", onScroll);
      observer?.disconnect();
    };
  }, [watchId]);

  const visible = active && scrolled && !blocked && !closed;
  const price = catalogPriceText(item, now);

  return (
    <div
      aria-hidden={!visible}
      className={`fixed inset-x-0 bottom-0 z-40 border-t border-line-strong bg-paper/95 px-3 pb-[calc(env(safe-area-inset-bottom)+8px)] pt-2 shadow-[0_-8px_24px_-12px_rgb(0_0_0/0.25)] backdrop-blur transition-transform duration-300 motion-reduce:transition-none lg:hidden ${
        visible ? "translate-y-0" : "pointer-events-none translate-y-full"
      }`}
    >
      <div className="mx-auto flex max-w-[640px] items-center gap-3">
        <a
          href={item.url}
          target="_blank"
          rel="sponsored nofollow noopener noreferrer"
          data-aff-pos="mobile-bar"
          tabIndex={visible ? 0 : -1}
          className="flex min-w-0 flex-1 items-center gap-3"
        >
          <span aria-hidden data-decorative="" className="grid size-12 shrink-0 place-items-center overflow-hidden rounded-lg border border-line bg-white">
            {item.image ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={item.image} alt="" fetchPriority="low" decoding="async" className="size-full object-contain p-1" />
            ) : (
              <span className="px-1 text-center text-[9px] font-bold leading-tight text-ink">{item.name}</span>
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5 text-meta font-bold">
              <span className="rounded border border-line-strong px-1 text-badge text-muted">PR</span>
              <span className="text-accent">掲載中の商品</span>
            </span>
            <span className="block truncate text-meta font-bold text-ink">{item.name}</span>
            {price && <span className="text-meta font-bold tabular-nums tracking-normal text-ink">{price}</span>}
          </span>
          <span className="shrink-0 rounded-md bg-accent px-3.5 py-2 text-meta font-bold text-accent-ink">見る</span>
        </a>
        <button
          type="button"
          aria-label="閉じる"
          tabIndex={visible ? 0 : -1}
          onClick={() => {
            setClosed(true);
            try {
              sessionStorage.setItem(STORAGE_KEY, "1");
            } catch {}
          }}
          className="grid size-8 shrink-0 place-items-center rounded-full text-muted hover:bg-surface"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </div>
    </div>
  );
}
