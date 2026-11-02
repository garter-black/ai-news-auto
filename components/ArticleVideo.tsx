"use client";

import { useState } from "react";
import type { ArticleVideoInfo } from "@/lib/articles";

/**
 * 記事の解説動画（サイト用の全部の版。scripts/sns_video_followup.py が記事 JSON の video に書く）。
 * 最初はポスター画像（遅延読み込み・16:9 の場所を先に取る）と再生ボタンだけ。押したときに初めて <video> を置いて再生する
 * （LCP・通信量に響かない。音つきの自動再生はしない）。動画は /video/ から Range つきで届く（cloudflare/video.ts）
 */
export default function ArticleVideo({ video }: { video: ArticleVideoInfo }) {
  const [playing, setPlaying] = useState(false);
  const seconds = video.seconds ? Math.round(video.seconds) : 0;
  const label = seconds ? `動画で解説（約${seconds}秒）` : "動画で解説";
  return (
    <figure className="mt-6">
      <div className="relative aspect-video overflow-hidden rounded-xl bg-surface">
        {playing ? (
          <video src={video.src} poster={video.poster || undefined} controls autoPlay playsInline preload="auto" className="size-full bg-ink" />
        ) : (
          <button type="button" onClick={() => setPlaying(true)} aria-label={`${label}を再生する`} className="group absolute inset-0 block size-full">
            {video.poster ? (
              // eslint-disable-next-line @next/next/no-img-element -- 遅延読み込みのポスター（next/image の最適化は使っていない）
              <img src={video.poster} alt="" width={1280} height={720} loading="lazy" decoding="async" fetchPriority="low" className="size-full object-cover" />
            ) : null}
            <span aria-hidden className="absolute inset-0 grid place-items-center">
              <span className="grid size-16 place-items-center rounded-full bg-ink/80 text-paper shadow-lg transition-transform group-hover:scale-105">
                <svg viewBox="0 0 24 24" className="ml-1 size-7" fill="currentColor"><path d="M8 5v14l11-7z" /></svg>
              </span>
            </span>
          </button>
        )}
      </div>
      <figcaption className="mt-2 text-meta text-muted">
        {label}
        {video.credit ? `・音声 ${video.credit}` : ""}
      </figcaption>
    </figure>
  );
}
