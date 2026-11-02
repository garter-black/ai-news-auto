import Link from "next/link";
import HeaderNav from "@/components/HeaderNav";
import LiveLine from "@/components/LiveLine";
import ThemeToggle from "@/components/ThemeToggle";
import { getAllArticles } from "@/lib/articles";
import { siteConfig } from "@/site.config";

// 上のメニューは5つまでに絞る（企業別はトップの企業チップから、まとめは記事一覧から辿れる）
const NAV: { href: string; label: string }[] = [
  { href: "/", label: "AI速報" },
  { href: "/usable", label: "使えるAI" },
  { href: "/news", label: "ニュース" },
  { href: "/products", label: "AIサービス図鑑" },
  { href: "/articles", label: "記事一覧" },
];

/**
 * 濃紺の帯（ロゴ・メニュー）と、その下の速報の1行。
 * 帯だけ画面上に残し、速報の1行はスクロールで流す（スマホで本文を押し下げすぎないため）。
 */
export default function Header() {
  // いちばん新しいAIの記事（AI速報サイトの看板の1行）
  // トップでは先頭の大きな記事と重ならないよう、2番目の記事を出す（LiveLine が切り替える）
  const [latest, second] = getAllArticles().filter((a) => a.genreSlug === "ai").slice(0, 2);

  return (
    <>
      <header className="site-header sticky top-0 z-50">
        <div className="mx-auto flex h-14 max-w-[1240px] items-center gap-6 px-4 sm:px-8">
          <Link href="/" className="flex shrink-0 items-baseline gap-3">
            <span className="font-display text-[1.35rem] leading-none text-mast-ink">{siteConfig.shortName}</span>
            <span className="hidden text-meta font-bold text-mast-muted sm:inline md:hidden xl:inline">{siteConfig.tagline}</span>
          </Link>

          <HeaderNav items={NAV} />

          <div className="ml-auto flex shrink-0 items-center gap-1.5 md:ml-0">
            {/* スマホではメニューを畳んでいるので、AI以外の話題への入口だけ出す */}
            <Link
              href="/news"
              className="rounded-sm border border-white/30 px-2.5 py-1 text-meta font-bold text-mast-ink transition-colors hover:border-white md:hidden"
            >
              ニュース
            </Link>
            <ThemeToggle />
          </div>
        </div>
      </header>

      {latest && (
        <LiveLine
          items={[latest, second ?? latest].map((a) => ({ slug: a.slug, title: a.title, publishedAt: a.publishedAt }))}
        />
      )}
    </>
  );
}
