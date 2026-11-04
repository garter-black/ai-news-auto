import { Martian_Mono } from "next/font/google";

// 本文の Noto Sans JP は next/font を使わず、必要な端末だけ後から読む（app/layout.tsx の先頭のスクリプト）
import localFont from "next/font/local";

/**
 * ロゴ「サイソク」と決まった見出しだけに使う極太。
 * Google Fonts のままだと @font-face が 120 個以上（全ページで描画を止める CSS が約 30KB）になるため、
 * 使う文字（英数字・かな・一部の漢字）だけに絞った版を同梱する（scripts/subset_dela_font.py で作る）
 */
export const dela = localFont({
  src: "./_fonts/dela-gothic-one-subset.woff2",
  weight: "400",
  display: "swap",
  preload: false,
  variable: "--font-dela",
});

/**
 * 時刻・件数・統合本数。小さい数字だけなので先読みはせず、LCP の画像・CSS に回線を譲る。
 * 届くまでの代わりの書体（幅を Martian Mono に合わせたもの）は app/globals.css の SaisokuMonoFallback。
 * next/font が作る代わりは Arial だけを指すため、Arial の無い Linux・Android では幅の狭い等幅に落ち、
 * 届いた瞬間に記事上部の日時の行が2行に折り返して見出しと要点が押し下げられていた（CLS 0.18、issue #24）
 */
export const martian = Martian_Mono({
  weight: ["400", "600"],
  subsets: ["latin"],
  display: "swap",
  preload: false,
  adjustFontFallback: false,
  fallback: ["SaisokuMonoFallback", "SaisokuMonoFallbackRoboto"],
  variable: "--font-martian",
});
