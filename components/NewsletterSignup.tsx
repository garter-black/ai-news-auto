"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { hasOwnSignup } from "@/lib/newsletter-placement";

/**
 * メール配信の登録欄。記事末・フッター・/newsletter に置く。
 * 目立たせすぎない（黄色・極太見出しは使わない）。書体はサイト共通の Noto Sans JP。
 */

type Variant = "article" | "footer" | "page";
type Phase = "idle" | "sending" | "sent" | "queued" | "error";

// サイト共通の Noto Sans JP（以前は細い Zen Kaku を避けるため OS の書体にしていた。游ゴシックは細いので使わない）
const FONT = "var(--font-sans)";

const ERRORS: Record<string, string> = {
  "invalid-email": "メールアドレスの形式を確認してください。",
  "rate-limited": "短時間に何度も送信されました。しばらくしてからお試しください。",
  unavailable: "ただいま登録を受け付けられません。時間をおいてお試しください。",
};

export default function NewsletterSignup({ variant = "article" }: { variant?: Variant }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = Date.now();
  }, []);
  const pathname = usePathname();

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPhase("sending");
    setError("");
    try {
      const res = await fetch("/api/newsletter/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: form.get("email"),
          company: form.get("company"),
          ts: shownAt.current,
          source: variant,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as { ok?: boolean; state?: string; error?: string };
      if (data.ok) {
        setPhase(data.state === "queued" ? "queued" : "sent");
      } else {
        setPhase("error");
        setError(ERRORS[data.error ?? ""] ?? ERRORS.unavailable);
      }
    } catch {
      setPhase("error");
      setError(ERRORS.unavailable);
    }
  }

  // 本文に登録欄があるページ（記事・/newsletter）では、フッターの登録欄は出さない（1ページに1つ。lib/newsletter-placement.ts）
  if (variant === "footer" && hasOwnSignup(pathname)) return null;

  const compact = variant === "footer";
  const done = phase === "sent" || phase === "queued";

  return (
    <section
      data-newsletter-signup={variant}
      aria-labelledby={variant === "page" ? undefined : `nl-${variant}`}
      aria-label={variant === "page" ? "メール配信の登録" : undefined}
      style={{ fontFamily: FONT }}
      className={
        compact
          ? "max-w-md"
          : variant === "page"
            ? "border-y border-line py-8"
            : "mt-12 max-w-[46em] border-t border-line pt-8"
      }
    >
      {variant !== "page" && (
        <>
        <p className="text-meta font-semibold tracking-[0.14em] text-muted">NEWSLETTER</p>
        <h2
          id={`nl-${variant}`}
          className={`mt-1.5 leading-snug ${compact ? "t-card" : "t-h3"}`}
        >
          毎朝7時、昨日のAIニュースの<span className="whitespace-nowrap">要点を5本</span>
        </h2>
        {!compact && (
          <p className="mt-2 text-desc leading-[1.8] text-ink-soft">
            前日に出た記事から大きな発表を選び、1本2行でまとめてメールで届けます。無料・いつでも解除できます。
          </p>
        )}
        </>
      )}

      {done ? (
        <p role="status" className="mt-4 border-l-2 border-accent pl-3 text-desc leading-[1.8] text-ink">
          {phase === "sent"
            ? "確認メールを送りました。メール内のリンクを開くと登録が完了します（届かない場合は迷惑メールフォルダもご確認ください）。"
            : "登録を受け付けました。配信の準備が整いしだい、確認メールをお送りします。"}
        </p>
      ) : (
        <form onSubmit={onSubmit} className={variant === "page" ? "" : "mt-4"}>
          <div className="flex flex-col gap-2 sm:flex-row">
            <label htmlFor={`nl-email-${variant}`} className="sr-only">
              メールアドレス
            </label>
            <input
              id={`nl-email-${variant}`}
              name="email"
              type="email"
              required
              autoComplete="email"
              inputMode="email"
              placeholder="you@example.com"
              maxLength={254}
              className="h-11 min-w-0 flex-1 rounded-lg border border-line-strong bg-paper px-3.5 text-[15px] text-ink placeholder:text-muted/70 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
            />
            {/* ボット除け（人には見えない） */}
            <div aria-hidden className="absolute -left-[9999px] h-px w-px overflow-hidden">
              <label>
                会社名
                <input name="company" type="text" tabIndex={-1} autoComplete="off" />
              </label>
            </div>
            <button
              type="submit"
              disabled={phase === "sending"}
              className="h-11 shrink-0 rounded-lg bg-ink px-5 text-note font-semibold text-paper transition-opacity hover:opacity-85 disabled:opacity-60"
            >
              {phase === "sending" ? "送信中…" : "無料で受け取る"}
            </button>
          </div>
          {phase === "error" && (
            <p role="alert" className="mt-2 text-meta text-live">
              {error}
            </p>
          )}
          <p className="mt-2.5 text-note leading-[1.7] text-muted">
            確認メールのリンクを開いた時点で登録完了です。アドレスは配信にだけ使います（
            <Link href="/privacy" className="underline underline-offset-2 hover:text-accent">
              プライバシーポリシー
            </Link>
            ）。
            {variant !== "page" && (
              <>
                {" "}
                <Link href="/newsletter" className="underline underline-offset-2 hover:text-accent">
                  配信の内容
                </Link>
              </>
            )}
          </p>
        </form>
      )}
    </section>
  );
}
