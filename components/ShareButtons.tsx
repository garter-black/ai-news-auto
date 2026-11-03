"use client";

import { useEffect, useState } from "react";
import { SHARE_PLATFORMS, shareHref, sharedUrl, type SharePlatform } from "@/lib/share";

/** 各 SNS の形（1色。文字色に合わせる） */
const ICONS: Record<SharePlatform | "link" | "share", React.ReactNode> = {
  x: <path d="M17.75 3h3.07l-6.71 7.67L22 21h-6.18l-4.84-6.33L5.45 21H2.38l7.18-8.2L2 3h6.33l4.38 5.79L17.75 3Zm-1.08 16.18h1.7L7.4 4.73H5.58l11.09 14.45Z" />,
  threads: (
    <path d="M16.6 11.1a5 5 0 0 0-.2-.1c-.2-2.5-1.6-4-4-4-1.4 0-2.6.6-3.3 1.7l1.3.9c.5-.8 1.3-1 2-1 .9 0 1.5.3 1.9.8.3.4.5.9.6 1.5a10 10 0 0 0-2.2-.1c-2.3.1-3.7 1.4-3.6 3.2.1 1.8 1.7 2.9 3.6 2.8 1.5-.1 2.6-.7 3.3-1.8.5-.8.8-1.8.9-3 .8.5 1.4 1.1 1.6 1.9.4 1.4.4 3.6-1.4 5.4-1.6 1.6-3.5 2.3-6.4 2.3-3.2 0-5.6-1-7.2-3S2.9 14.3 2.9 12s.6-4.4 1.8-6c1.5-2 3.9-3 7.2-3 3.3 0 5.7 1 7.3 3 .8 1 1.4 2.2 1.8 3.6l1.6-.4c-.4-1.7-1.1-3.1-2.1-4.3C18.6 2.3 15.7 1.2 12 1.2 8.2 1.2 5.4 2.4 3.6 4.7 2 6.8 1.2 9.6 1.2 12s.8 5.2 2.4 7.3c1.8 2.3 4.6 3.5 8.4 3.5 3.3 0 5.6-.9 7.5-2.8 2.5-2.5 2.4-5.6 1.6-7.5-.5-1.2-1.4-2.2-2.5-2.9l-2 1.5Zm-4.5 4.2c-.9 0-1.9-.4-1.9-1.3-.1-.6.4-1.4 2.1-1.5h.6c.6 0 1.2.1 1.7.2-.2 2.2-1.3 2.6-2.5 2.6Z" />
  ),
  facebook: (
    <path d="M13.4 21.9v-7.7h2.6l.4-3h-3V9.3c0-.9.2-1.5 1.5-1.5h1.6V5.1a22 22 0 0 0-2.4-.1c-2.4 0-4 1.4-4 4.1v2.2H7.5v3h2.6v7.6a10 10 0 1 1 3.3 0Z" />
  ),
  line: (
    <path d="M12 2.8c-5.5 0-10 3.6-10 8.1 0 4 3.6 7.4 8.4 8 .3.1.8.2.9.5.1.3.1.7 0 .9l-.1.9c0 .3-.2 1 .9.5 1.1-.5 5.9-3.5 8-5.9 1.5-1.6 2.2-3.2 2.2-4.9 0-4.5-4.5-8.1-10.3-8.1ZM7.9 13.5H6a.5.5 0 0 1-.5-.5V9.1a.5.5 0 0 1 1 0v3.4h1.4a.5.5 0 0 1 0 1Zm2-.5a.5.5 0 0 1-1 0V9.1a.5.5 0 0 1 1 0V13Zm4.6 0a.5.5 0 0 1-.9.3l-2-2.7V13a.5.5 0 0 1-1 0V9.1a.5.5 0 0 1 .9-.3l2 2.7V9.1a.5.5 0 0 1 1 0V13Zm3.1-2.4a.5.5 0 0 1 0 1h-1.4v.9h1.4a.5.5 0 0 1 0 1h-1.9a.5.5 0 0 1-.5-.5V9.1c0-.3.2-.5.5-.5h1.9a.5.5 0 0 1 0 1h-1.4v.9h1.4Z" />
  ),
  bluesky: (
    <path d="M6.3 4.2C8.6 5.9 11 9.4 12 11.4c1-2 3.4-5.5 5.7-7.2 1.6-1.2 4.3-2.2 4.3.9 0 .6-.4 5.2-.6 5.9-.7 2.6-3.4 3.2-5.7 2.8 4.1.7 5.1 3 2.9 5.3-4.3 4.4-6.1-1.1-6.6-2.5l-.1-.3-.1.3c-.5 1.4-2.3 6.9-6.6 2.5-2.3-2.3-1.2-4.6 2.9-5.3-2.3.4-5-.2-5.7-2.8C2.3 10.3 2 5.7 2 5.1c0-3.1 2.7-2.1 4.3-.9Z" />
  ),
  link: (
    <path
      d="M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
    />
  ),
  share: (
    <path
      d="M12 3v12M7.5 7.5 12 3l4.5 4.5M6 11H5v9h14v-9h-1"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
};

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="size-[18px]" fill="currentColor">
      {ICONS[name]}
    </svg>
  );
}

const BTN =
  "grid size-9 shrink-0 place-items-center rounded-md border border-line text-ink-soft transition-colors hover:border-accent hover:text-accent";

/**
 * 記事のシェアボタン（X・Threads・Facebook・LINE・Bluesky・リンクをコピー）。
 * どのボタンも普通のリンクなので JS が無くても動き、外部のスクリプトは読まない。
 * 指で操作する端末（スマホ）では先頭に「共有」を出し、端末の共有画面（Web Share API）を開く。
 * 「共有」の枠は CSS（pointer: coarse）で最初から取っておき、表示後に並びが動かない（CLS 0）。
 * 共有画面が無いブラウザでは、同じ枠のままリンクのコピーになる。
 */
export default function ShareButtons({ url, title, className = "" }: { url: string; title: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const [canShare, setCanShare] = useState(false);

  useEffect(() => {
    setCanShare(typeof navigator !== "undefined" && typeof navigator.share === "function");
  }, []);

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(t);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(sharedUrl(url, "share_copy"));
      setCopied(true);
    } catch {
      // クリップボードが使えないときは、アドレスを選べる形で見せる
      window.prompt("このアドレスをコピーしてください", sharedUrl(url, "share_copy"));
    }
  }

  async function nativeShare() {
    if (!canShare) return copy();
    try {
      await navigator.share({ title, url: sharedUrl(url, "share_native") });
    } catch {
      // 共有画面を閉じただけのときは何もしない
    }
  }

  return (
    <div className={`relative flex items-center gap-1.5 ${className}`}>
      <span className="mr-1 hidden text-meta font-bold text-muted pointer-fine:inline">シェア</span>
      <button
        type="button"
        onClick={nativeShare}
        className="hidden h-9 shrink-0 items-center gap-1.5 rounded-md bg-accent px-3 text-meta font-bold text-accent-ink pointer-coarse:inline-flex"
      >
        <Icon name="share" />
        共有
      </button>
      {SHARE_PLATFORMS.map((p) => (
        <a
          key={p.id}
          href={shareHref(p.id, url, title)}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${p.label}でシェア`}
          title={`${p.label}でシェア`}
          className={BTN}
        >
          <Icon name={p.id} />
        </a>
      ))}
      <button type="button" onClick={copy} aria-label="記事のリンクをコピー" title="リンクをコピー" className={BTN}>
        <Icon name="link" />
      </button>
      <span
        role="status"
        aria-live="polite"
        className={`pointer-events-none absolute -top-8 right-0 rounded-md bg-ink px-2 py-1 text-meta font-bold text-paper transition-opacity ${
          copied ? "opacity-100" : "opacity-0"
        }`}
      >
        {copied ? "リンクをコピーしました" : ""}
      </span>
    </div>
  );
}
