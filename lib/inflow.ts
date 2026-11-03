/**
 * 記事に「外から来た人」の数え方（画面 components/InflowBeacon.tsx と Worker cloudflare/inflow.ts が共有する決まり）。
 * 集計は scripts/inflow_report.py。参照元の原文・検索語・クエリ全体はサーバーへ送らない（流入元の分類だけ）。
 */
import { BOT_UA } from "./revenue-funnel";

export const INFLOW_SOURCES = [
  "threads", "x", "facebook", "bluesky", "google", "yahoo", "bing", "discover", "news", "newsletter", "line", "direct", "other",
] as const;
export type InflowSource = typeof INFLOW_SOURCES[number];

/**
 * 数えない UA。既存の BOT_UA（bot・headless・lighthouse・pagespeed・自動ブラウザ・curl など）に、
 * SNS のリンクプレビュー（facebookexternalhit など。"bot" を含まないもの）と速度計測サービスを足す。
 * プレビュー取得は JS を動かさないのでもともと送信しないが、念のため UA でも落とす
 */
export const INFLOW_BOT_UA = new RegExp(
  `${BOT_UA.source}|facebookexternalhit|cardyb|facebookcatalog|meta-externalagent|meta-externalfetcher|embedly|iframely|whatsapp|telegram|discord|skypeuripreview|vkshare|chrome-lighthouse|gtmetrix|ptst|webpagetest|axios|node-fetch|go-http|okhttp|java\\/|httpclient|electron`,
  "i",
);

/** 自社のテスト・確認用のしるし（AGENTS.md「自社アクセスを数えない」） */
export const INTERNAL_PARAM = "_internal";
export const INTERNAL_HEADER = "x-saisoku-internal";
export const INTERNAL_STORAGE_KEY = "saisoku-internal";

const SLUG = /^[A-Za-z0-9][A-Za-z0-9_-]{0,160}$/;
const TAG = /^[a-z0-9][a-z0-9_.-]{0,39}$/;

/** 数えるページ → 集計の名前。記事は slug、朝のまとめは digest-<YYYYMMDD>（state/threads_posted.json のキーと同じ）、トップは _home */
export function pageKey(pathname: string): string | null {
  const article = /^\/articles\/([^/]+)\/?$/.exec(pathname);
  if (article) {
    let slug = article[1];
    try { slug = decodeURIComponent(slug); } catch { return null; }
    return SLUG.test(slug) ? slug : null;
  }
  const digest = /^\/digest\/(\d{8})\/?$/.exec(pathname);
  if (digest) return `digest-${digest[1]}`;
  return pathname === "/" ? "_home" : null;
}
export const validPageKey = (key: string) => SLUG.test(key) || key === "_home";
export const validTag = (value: string) => value === "" || TAG.test(value);

const UTM_ALIASES: Record<string, InflowSource> = {
  twitter: "x", "t.co": "x", fb: "facebook", ig: "other", bsky: "bluesky", gnews: "news",
};

/** 参照元のホスト名 → 流入元。同じサイト内からの移動は null（数えない） */
export function sourceFromReferrer(referrer: string, origin: string): InflowSource | null {
  if (!referrer) return "direct";
  // Android の Google アプリ（Discover・検索アプリ）は android-app:// で来る
  if (/^android-app:\/\/com\.google\.android\.googlequicksearchbox/i.test(referrer)) return "discover";
  if (/^android-app:\/\/com\.google\.android\.gm/i.test(referrer)) return "other";
  let ref: URL;
  try { ref = new URL(referrer); } catch { return "other"; }
  if (ref.origin === origin) return null;
  const host = ref.hostname.toLowerCase().replace(/^www\./, "");
  if (/(^|\.)threads\.(net|com)$/.test(host)) return "threads";
  if (/^(t\.co|x\.com|twitter\.com|mobile\.twitter\.com|mobile\.x\.com)$/.test(host)) return "x";
  if (/(^|\.)(facebook\.com|fb\.com|fb\.me|messenger\.com)$/.test(host)) return "facebook";
  if (/(^|\.)(bsky\.app|bsky\.social)$/.test(host)) return "bluesky";
  if (/^news\.google\./.test(host) || /^news\.yahoo\.co\.jp$/.test(host) || /(^|\.)(smartnews\.com|gunosy\.com|newspicks\.com|msn\.com|line\.me|antenna\.jp|nordot\.app)$/.test(host)) {
    return host.endsWith("line.me") ? "line" : "news";
  }
  if (/^discover\.google\./.test(host)) return "discover";
  if (/(^|\.)google\.[a-z.]+$/.test(host) || host === "googleapis.com") return "google";
  if (/(^|\.)yahoo\.(co\.jp|com)$/.test(host)) return "yahoo";
  if (/(^|\.)bing\.com$/.test(host)) return "bing";
  return "other";
}

export type Inflow = { source: InflowSource; campaign: string; content: string };

/** URL のクエリと参照元から流入元を決める。utm_source があればそれを優先。サイト内の移動・テストは null */
export function classifyInflow(search: string, referrer: string, origin: string): Inflow | null {
  const params = new URLSearchParams(search);
  if (params.get(INTERNAL_PARAM) === "1") return null;
  const tag = (name: string) => {
    const value = (params.get(name) || "").toLowerCase().slice(0, 40);
    return TAG.test(value) ? value : "";
  };
  const utm = tag("utm_source");
  if (utm) {
    const source = UTM_ALIASES[utm] ?? (INFLOW_SOURCES.includes(utm as InflowSource) && utm !== "direct" ? utm as InflowSource : "other");
    return { source, campaign: tag("utm_campaign"), content: tag("utm_content") };
  }
  const source = sourceFromReferrer(referrer, origin);
  return source ? { source, campaign: "", content: "" } : null;
}
