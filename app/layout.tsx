import type { Metadata } from "next";
import dynamic from "next/dynamic";
import DeferredScript from "@/components/DeferredScript";
import Header from "@/components/Header";
import Footer from "@/components/Footer";
import RevenueFunnelTracker from "@/components/RevenueFunnelTracker";
import AffClickTracker from "@/components/AffClickTracker";
import InflowBeacon from "@/components/InflowBeacon";
import { siteConfig } from "@/site.config";
import { publisherJsonLd } from "@/lib/publisher";
import { dela, martian } from "./fonts";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(siteConfig.url),
  title: {
    // 強み（最速・日本語・使えるかの判定）が伝わる題名。区切りの縦棒は使わない
    default: `${siteConfig.siteName} AI・ビジネス・お金の新しい動きが最速でわかる`,
    template: `%s - ${siteConfig.name}`,
  },
  description: siteConfig.description,
  applicationName: siteConfig.siteName,
  // SNS と同じブランド画像を、変更頻度の低いURLで検索エンジンにも明示する。
  // /icon・/apple-icon の旧URLはフィードや既存端末向けに引き続き配信する。
  icons: {
    icon: [{ url: "/icons/icon-512.png", sizes: "512x512", type: "image/png" }],
    apple: [{ url: "/icons/apple-icon-180.png", sizes: "180x180", type: "image/png" }],
  },
  // iPhone で「ホーム画面に追加」したときの名前（プッシュ通知はホーム画面から開いたときだけ使える）。manifest は app/manifest.ts
  appleWebApp: { capable: true, title: siteConfig.shortName, statusBarStyle: "default" },
  // Google Search Console の所有権確認。環境変数に確認コードを入れるだけで有効になる
  verification: process.env.GOOGLE_SITE_VERIFICATION
    ? { google: process.env.GOOGLE_SITE_VERIFICATION }
    : undefined,
  authors: [{ name: siteConfig.author }],
  alternates: {
    canonical: "/",
    types: { "application/rss+xml": "/feed.xml" },
  },
  openGraph: {
    type: "website",
    siteName: siteConfig.siteName,
    locale: siteConfig.locale,
    // og:url はここに置かない（openGraph を持たない下層ページ（/tools など）が og:url=トップを引き継ぎ、
    // Facebook・LINE でシェアするとトップのシェアとして扱われていた）。トップの og:url は app/page.tsx。
    // og:title・og:description も置かない（同じ理由で、下層ページがトップの題名でシェアされていた。2026-11-03）。
    // 置かないページでは SNS がページの <title>・説明文を使う。主な固定ページは lib/page-share.ts で題名・画像を出す
  },
  twitter: {
    card: "summary_large_image",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
};

/**
 * Vercel Analytics。Cloudflare では NEXT_PUBLIC_VERCEL_ANALYTICS=off で外す（scripts/cf_build.sh）。
 * 外すときはビルドで読み込み自体が消えるよう、使うときだけ読み込む（全ページの JS から約 2KB 減る）
 */
const VercelAnalytics =
  process.env.NEXT_PUBLIC_VERCEL_ANALYTICS !== "off" ? dynamic(() => import("@vercel/analytics/next").then((m) => m.Analytics)) : null;

/** 本文の Noto Sans JP（可変フォント 1 本で 400〜900。docs/typography.md） */
const NOTO_CSS = "https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400..900&display=swap";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    // Google の検索結果でアイコンの横に出るサイト名
    name: siteConfig.siteName,
    alternateName: publisherJsonLd.alternateName,
    url: siteConfig.url,
    description: siteConfig.description,
    inLanguage: "ja",
    publisher: publisherJsonLd,
    // サイト内検索（/search?q=…）の入口
    potentialAction: {
      "@type": "SearchAction",
      target: { "@type": "EntryPoint", urlTemplate: `${siteConfig.url}/search?q={search_term_string}` },
      "query-input": "required name=search_term_string",
    },
  };

  const adsenseClient = process.env.NEXT_PUBLIC_ADSENSE_CLIENT;
  const cfBeaconToken = process.env.NEXT_PUBLIC_CF_BEACON_TOKEN;

  return (
    <html
      lang={siteConfig.lang}
      className={`${dela.variable} ${martian.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* AdSense のサイト所有権確認用（ads.txt と併用が推奨） */}
        {adsenseClient && (
          <meta name="google-adsense-account" content={adsenseClient} />
        )}
        {/* 壊れた画像アイコンを読者に見せない受け皿（React が動き出す前の失敗も拾う）。
            data-fallback があればそこ（記事の独自カバー /cover/<slug>）へ、無ければ透明な画像に替えて枠の地色を見せる。
            記事のサムネイル部品（components/Thumb.tsx、data-thumb）は自前の段階的な受け皿を持つので触らない */}
        {/* 描画前にテーマを確定させ、リロード時の白フラッシュを防ぐ。
            本文の Noto Sans JP（Google Fonts）は、内蔵の日本語書体が使えない端末（Windows など）だけ読み込む。
            Apple はヒラギノ、Android は内蔵の Noto Sans CJK（同じ字形）で足りる。
            スクリプトから足したスタイルシートは描画を止めないので、@font-face 120 個分の CSS（約 33KB）が全端末の初回表示から外れる */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var u=navigator.userAgent;if(!/Android|iPhone|iPad|Macintosh|Mac OS X/.test(u)){var h=document.head,c=document.createElement("link");c.rel="preconnect";c.href="https://fonts.gstatic.com";c.crossOrigin="";h.appendChild(c);var l=document.createElement("link");l.rel="stylesheet";l.href="${NOTO_CSS}";h.appendChild(l)}}catch(e){}try{var t=localStorage.getItem("theme");if(t==="dark"||t==="light")document.documentElement.setAttribute("data-theme",t)}catch(e){}try{var B="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";document.addEventListener("error",function(e){var i=e.target;if(!i||i.tagName!=="IMG"||i.hasAttribute("data-thumb"))return;var f=i.getAttribute("data-fallback");i.removeAttribute("srcset");if(f&&!i.hasAttribute("data-fell")){i.setAttribute("data-fell","");i.src=f}else if(i.src!==B){i.src=B}},true)}catch(e){}`,
          }}
        />
      </head>
      <body className="flex min-h-dvh flex-col bg-paper text-ink">
        <script
          type="application/ld+json"
          suppressHydrationWarning
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />

        {/* AdSense 本体。NEXT_PUBLIC_ADSENSE_CLIENT を設定すると自動で有効化される。
            ページの読み込みが終わってから、読者の操作かブラウザが空いたとき（3.5秒後以降）に読む。
            早く読むと本文・写真の表示や最初の操作と回線・CPU を取り合う（LCP・INP が遅れる）。
            広告枠（AdSlot）の push はスクリプトより先に積んでおけるので、遅らせても枠は埋まる */}
        {adsenseClient && (
          <DeferredScript
            id="adsbygoogle-init"
            crossOrigin="anonymous"
            src={`https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${adsenseClient}`}
          />
        )}
        <Header />
        <main className="flex-1">{children}</main>
        <Footer />
        {/* 提携リンクの人によるクリックを数える（ASP の数字と見比べる用。cloudflare/aff.ts） */}
        <AffClickTracker />
        <RevenueFunnelTracker />
        <InflowBeacon />
        {/* Vercelダッシュボードで Web Analytics を有効にした時点で計測が始まる。
            無効のうちは何も送信されないので、先に仕込んでおいて問題ない。
            Cloudflare では NEXT_PUBLIC_VERCEL_ANALYTICS=off で外す */}
        {VercelAnalytics && <VercelAnalytics />}
        {/* Cloudflare Web Analytics。トークンを設定したときだけ読み込む（表示が終わってから） */}
        {cfBeaconToken && (
          <DeferredScript
            id="cf-beacon"
            delayMs={0}
            src="https://static.cloudflareinsights.com/beacon.min.js"
            dataset={{ cfBeacon: JSON.stringify({ token: cfBeaconToken }) }}
          />
        )}
      </body>
    </html>
  );
}
