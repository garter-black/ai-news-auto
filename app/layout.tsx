import type { Metadata } from "next";
import Script from "next/script";
import { Analytics } from "@vercel/analytics/next";
import Header from "@/components/Header";
import Footer from "@/components/Footer";
import RevenueFunnelTracker from "@/components/RevenueFunnelTracker";
import AffClickTracker from "@/components/AffClickTracker";
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
    // Facebook・LINE でシェアするとトップのシェアとして扱われていた）。トップの og:url は app/page.tsx
    title: `${siteConfig.siteName} AI・ビジネス・お金の新しい動きが最速でわかる`,
    description: siteConfig.description,
  },
  twitter: {
    card: "summary_large_image",
    title: siteConfig.name,
    description: siteConfig.description,
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
};

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
        {/* 描画前にテーマを確定させ、リロード時の白フラッシュを防ぐ。
            本文の Noto Sans JP（Google Fonts）は、内蔵の日本語書体が使えない端末（Windows など）だけ読み込む。
            Apple はヒラギノ、Android は内蔵の Noto Sans CJK（同じ字形）で足りる。
            スクリプトから足したスタイルシートは描画を止めないので、@font-face 120 個分の CSS（約 33KB）が全端末の初回表示から外れる */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var u=navigator.userAgent;if(!/Android|iPhone|iPad|Macintosh|Mac OS X/.test(u)){var h=document.head,c=document.createElement("link");c.rel="preconnect";c.href="https://fonts.gstatic.com";c.crossOrigin="";h.appendChild(c);var l=document.createElement("link");l.rel="stylesheet";l.href="${NOTO_CSS}";h.appendChild(l)}}catch(e){}try{var t=localStorage.getItem("theme");if(t==="dark"||t==="light")document.documentElement.setAttribute("data-theme",t)}catch(e){}`,
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
            ページの読み込み後に読む（先読みすると本文・写真の表示と回線を取り合い LCP が遅れる） */}
        {adsenseClient && (
          <Script
            id="adsbygoogle-init"
            async
            strategy="lazyOnload"
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
        {/* Vercelダッシュボードで Web Analytics を有効にした時点で計測が始まる。
            無効のうちは何も送信されないので、先に仕込んでおいて問題ない。
            Cloudflare では NEXT_PUBLIC_VERCEL_ANALYTICS=off で外す */}
        {process.env.NEXT_PUBLIC_VERCEL_ANALYTICS !== "off" && <Analytics />}
        {/* Cloudflare Web Analytics。トークンを設定したときだけ読み込む */}
        {cfBeaconToken && (
          <Script
            id="cf-beacon"
            strategy="afterInteractive"
            src="https://static.cloudflareinsights.com/beacon.min.js"
            data-cf-beacon={JSON.stringify({ token: cfBeaconToken })}
          />
        )}
      </body>
    </html>
  );
}
