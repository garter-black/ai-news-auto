import type { Metadata } from "next";
import { siteConfig } from "@/site.config";

/**
 * 一覧・ツールなどの固定ページのシェア用の情報（og:title・og:description・og:image）。2026-11-03
 *
 * openGraph を書かないページは、レイアウトのトップページ用の og:title（「サイソクニュース AI・ビジネス・お金の…」）を
 * 引き継いでいた（/tools・/guides など）。ページごとの題名と、ページ名入りのシェア画像（app/og/[key]/route.tsx。
 * /news/share.png と同じ報道ネイビー×速報レッドの形）を出す。
 */
export type PageShare = {
  /** 画像の大きな見出し（短く。全角9字くらいまで） */
  heading: string;
  /** 見出しの下の2行 */
  lines: [string, string];
  /** 下の帯の1行 */
  footer: string;
};

export const PAGE_SHARE = {
  tools: { heading: "計算ツール", lines: ["ドル建てサブスクの円換算・AIプランの費用", "防衛特別所得税の年ごとの税額"], footer: "公式の数字と当日の為替で計算" },
  "usd-jpy": { heading: "サブスクの円換算", lines: ["ドル建ての月額を、今日の為替で円に", "消費税10%・年額つき"], footer: "サイソクの計算ツール" },
  "defense-tax": { heading: "防衛特別所得税", lines: ["2027年から始まる新しい税", "自分の場合の税額を年ごとに計算"], footer: "サイソクの計算ツール" },
  "ai-plan-cost": { heading: "AIプランの費用", lines: ["ChatGPT・Claude・Geminiの有料プラン", "月払いと年払い・円でいくら"], footer: "サイソクの計算ツール" },
  "ai-pricing": { heading: "AIの料金早見表", lines: ["ChatGPT・Claude・GeminiのAPIと月額プラン", "日本で使えるか・無料で試せるかも"], footer: "数字はすべて出典の記事で確認" },
  usable: { heading: "今すぐ使えるAI", lines: ["発表されたばかりの新機能・新モデルから", "今日から使えるものだけを毎日更新"], footer: "日本から使えるか・料金・無料かを判定" },
  guides: { heading: "AIの使い方ガイド", lines: ["始め方・料金・無料でどこまで使えるか", "公式情報をもとに1ページで"], footer: "サイソクのガイド" },
  products: { heading: "AIサービス図鑑", lines: ["できること・料金・最新ニュース", "主なAIサービスを1か所で"], footer: "サイソクのガイド" },
  compare: { heading: "比較ガイド", lines: ["AI時代の道具を、公式情報で比べる", "料金・できること・向いている人"], footer: "サイソクのガイド" },
  digest: { heading: "朝のAIニュース", lines: ["毎朝7時30分に、昨夜から今朝までの", "AIニュースを3本だけ"], footer: "最速で、要点だけ。" },
  weekly: { heading: "今週のAIニュース", lines: ["1週間の大きな発表を、まとめて", "仕事・お金にどう関係するかまで"], footer: "最速で、要点だけ。" },
  newsletter: { heading: "メール配信", lines: ["毎朝7時に、前日のAIニュースから5本", "1本2行の要点とリンクで。無料"], footer: "いつでも解除できます" },
  about: { heading: "運営者情報", lines: ["サイソクニュースの運営者と", "記事の作り方・編集方針"], footer: "公式発表から最短数分で" },
  "editorial-policy": { heading: "編集方針", lines: ["記事の作り方・AIの使い方", "事実の確かめ方・訂正のしかた"], footer: "サイソクニュースの約束" },
  corrections: { heading: "訂正履歴", lines: ["公開後に訂正した記事の一覧", "いつ・何を直したかを公開"], footer: "サイソクニュースの約束" },
} satisfies Record<string, PageShare>;

export type PageShareKey = keyof typeof PAGE_SHARE;

export const PAGE_SHARE_KEYS = Object.keys(PAGE_SHARE) as PageShareKey[];

/** シェア画像の URL（1200×630） */
export const pageShareImage = (key: PageShareKey) => `/og/${key}`;

/** ページの openGraph・twitter（題名・説明・URL・シェア画像） */
export function pageShareMetadata(
  key: PageShareKey,
  { title, description, url }: { title: string; description: string; url: string },
): Pick<Metadata, "openGraph" | "twitter"> {
  const image = { url: pageShareImage(key), width: 1200, height: 630, alt: title };
  return {
    openGraph: { type: "website", siteName: siteConfig.siteName, locale: siteConfig.locale, url, title, description, images: [image] },
    twitter: { card: "summary_large_image", title, description, images: [image.url] },
  };
}

/** 固定ページの metadata に openGraph・twitter（ページの題名・説明・シェア画像）を足す。ogTitle を省くと title を使う */
export function withPageShare(
  key: PageShareKey,
  url: string,
  meta: Metadata & { title: string; description: string },
  ogTitle?: string,
): Metadata {
  return { ...meta, ...pageShareMetadata(key, { title: ogTitle ?? meta.title, description: meta.description, url }) };
}
