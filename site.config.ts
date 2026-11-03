/**
 * サイト全体の設定。ここだけ書き換えればブランディングが一括で変わる。
 * NEXT_PUBLIC_SITE_URL は Vercel の環境変数で本番ドメインに差し替える。
 */
export const siteConfig = {
  // 2026-11-02 に「サイソクAIニュース」から改名（SNS 4つと同じ名前）。旧名は formerName として構造化データに残す
  name: "サイソクニュース",
  shortName: "サイソクニュース",
  // 本文で使う略称（「サイソクの判断」など）
  nickname: "サイソク",
  // 検索結果でアイコンの横に出るサイト名
  siteName: "サイソクニュース",
  // 旧名。JSON-LD の alternateName に入れ、Google に同じ媒体だと伝える（消さない）
  formerName: "サイソクAIニュース",
  // 「催促」と読み違えられないよう、題名では「最速」を併記する（app/layout.tsx）
  tagline: "仕事とお金の“旬”を、どこよりも速く。",
  // 実際に扱っている中身に合わせる（2026-11-03 オーナー決定・台帳 W14）。ジャンルの名前は、定期的に記事があるものだけ
  // ページ側で足す（lib/coverage.ts）。ここには、いつでも本当のこと（軸と約束）だけを書く
  description:
    "AI・テクノロジーを軸に、仕事とお金に関わるニュースを公式発表から最短数分で。「自分にどう関係する？何をすればいい？」まで3行で。",
  url:
    process.env.NEXT_PUBLIC_SITE_URL ??
    (process.env.VERCEL_PROJECT_PRODUCTION_URL
      ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
      : `http://localhost:${process.env.PORT ?? 3000}`),
  locale: "ja_JP",
  lang: "ja",
  author: "サイソク編集部",

  /**
   * 運営者情報。AdSense・ASPの審査では実在の運営者と連絡手段の明示が必須です。
   * ここを実際の情報に差し替えてください（未設定のうちは審査に通りません）。
   */
  operator: {
    name: "サイソク編集部",
    representative: "小室勇世",
    location: "東京都",
    // 独自ドメインの窓口（Cloudflare Email Routing でオーナーの受信箱へ転送。2026-11-03 から）
    email: "contact@ai-tech-express.jp",
    established: "2026年7月",
  },

  // 公式SNS（構造化データの sameAs と足元のリンクに使う。Google にサイトと同じ運営だと伝える）
  social: [
    { label: "X", url: "https://x.com/saisoku_ai" },
    { label: "Threads", url: "https://www.threads.com/@saisoku637" },
    { label: "Bluesky", url: "https://bsky.app/profile/ai-tech-express.jp" },
  ],

  // トップに出す記事件数
  topArticles: 24,
} as const;

export type SiteConfig = typeof siteConfig;
