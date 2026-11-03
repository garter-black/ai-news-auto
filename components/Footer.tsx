import Link from "next/link";
import CompanyMark from "@/components/CompanyMark";
import NewsletterSignup from "@/components/NewsletterSignup";
import DifyDecisionLink from "@/components/DifyDecisionLink";
import { ROUNDUP_CHANNEL, getAllArticles, getAllChannels, getAllTags } from "@/lib/articles";
import { companyCounts } from "@/lib/companies";
import { hasActiveAffiliate } from "@/lib/monetization";
import { genreNav } from "@/lib/nav";
import { siteConfig } from "@/site.config";

const LEGAL_LINKS = [
  { href: "/about", label: "運営者情報" },
  { href: "/editorial-policy", label: "編集方針・AI利用と事実確認" },
  { href: "/corrections", label: "訂正履歴" },
  { href: "/privacy", label: "プライバシーポリシー" },
  { href: "/disclaimer", label: "免責事項・広告について" },
  { href: "/contact", label: "お問い合わせ" },
];

// 何度も見に来てもらう定番ページ（ヘッダーはジャンルのタブが中心なので、ここにもまとめて置く）
const REFERENCE_LINKS = [
  { href: "/search", label: "記事を検索" },
  { href: "/news", label: "ジャンル別のニュース" },
  { href: "/usable", label: "今日から使えるAI" },
  { href: "/guides", label: "AIの使い方ガイド" },
  { href: "/ai-pricing", label: "AIの料金・日本対応 早見表" },
  { href: "/tools", label: "計算ツール（円換算・税金）" },
  { href: "/products", label: "AIサービス図鑑" },
  { href: "/choose/internet-for-ai", label: "AIのための通信回線の選び方" },
  { href: "/choose/dify-hosting", label: "Difyの無料利用・VPSの選び方" },
  { href: "/choose/ai-career", label: "AI転職・学び直しの選び方" },
  { href: "/weekly", label: "今週のAIニュースまとめ" },
  { href: "/events", label: "AIイベントの日程（日本時間）" },
];

export default function Footer() {
  const ai = getAllArticles().filter((a) => a.genreSlug === "ai");
  // 発信元はジャンルを問わず記事の多い順（AIだけに絞らない）。足元が伸びないよう上限を設ける
  // まとめ記事しかない発信元（名前が「◯本の記事から編集部が統合」のまま）は出さない
  const channels = getAllChannels().filter((c) => !ROUNDUP_CHANNEL.test(c.label)).slice(0, 24);
  const genres = genreNav();
  const companies = companyCounts(ai).slice(0, 8);
  const tags = getAllTags(true).slice(0, 16);

  return (
    <footer data-funnel-pos="footer" className="mt-24 border-t-2 border-ink bg-surface">
      <div className="mx-auto max-w-[1240px] px-5 py-14 sm:px-8">
        <div className="grid gap-12 md:grid-cols-[1.4fr_1fr_1fr]">
          <div>
            <p className="font-display text-2xl leading-none text-ink">
              {siteConfig.name}
            </p>
            <p className="mt-2 text-meta font-bold text-muted">
              {siteConfig.tagline}
            </p>
            <p className="mt-4 max-w-md text-note leading-[1.9] text-ink-soft">
              {siteConfig.description}
            </p>

            <div className="mt-8">
              <NewsletterSignup variant="footer" />
            </div>

            <p className="mt-8 max-w-md text-note leading-[1.8] text-muted">
              当サイトはA8.netなどのアフィリエイトプログラムに参加しており、記事内に広告（プロモーション）を含む場合があります。
              <span className="mt-1 block">Amazonのアソシエイトとして、{siteConfig.siteName}は適格販売により収入を得ています。</span>
            </p>

            <p className="mt-6 text-note font-bold text-ink">
              公式SNS：
              {siteConfig.social.map((s, i) => (
                <span key={s.url}>
                  {i > 0 && " ・ "}
                  <a href={s.url} target="_blank" rel="me noopener" className="text-accent underline-offset-4 hover:underline">
                    {s.label}
                  </a>
                </span>
              ))}
            </p>

            <ul className="mt-6 space-y-2">
              {LEGAL_LINKS.map((link) => (
                <li key={link.href}>
                  <Link
                    href={link.href}
                    className="text-note font-medium text-ink-soft underline-offset-4 hover:text-accent hover:underline"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <p className="eyebrow">GENRES</p>
            <ul className="mb-8 mt-4 grid grid-cols-2 gap-x-4 gap-y-2.5">
              {genres.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-note font-semibold text-ink-soft hover:text-accent">
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>

            <p className="eyebrow">REFERENCE</p>
            <ul className="mb-8 mt-4 space-y-2.5">
              {REFERENCE_LINKS.map((link) => (
                <li key={link.href}>
                  {link.href === "/choose/dify-hosting" ? (
                    <DifyDecisionLink className="text-note font-semibold text-ink-soft hover:text-accent">{link.label}</DifyDecisionLink>
                  ) : (
                    <Link href={link.href} className="text-note font-semibold text-ink-soft hover:text-accent">
                      {link.label}
                    </Link>
                  )}
                </li>
              ))}
            </ul>

            <p className="eyebrow">COMPANIES</p>
            <ul className="mt-4 space-y-2.5">
              {companies.map(({ company, count }) => (
                <li key={company.slug}>
                  <Link
                    href={`/companies/${company.slug}`}
                    className="inline-flex items-center gap-2 text-note font-semibold text-ink-soft hover:text-accent"
                  >
                    <CompanyMark company={company} size={16} />
                    {company.name}
                    <span className="index-num text-meta text-muted">{count}</span>
                  </Link>
                </li>
              ))}
            </ul>

            {channels.length > 0 && (
              <>
                <p className="eyebrow mt-8">SOURCES</p>
                <ul className="mt-4 space-y-2">
                  {channels.map((channel) => (
                    <li key={channel.slug}>
                      <Link
                        href={`/channels/${channel.slug}`}
                        className="text-note text-muted hover:text-accent"
                      >
                        {channel.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          {tags.length > 0 && (
            <div>
              <p className="eyebrow">TOPICS</p>
              <ul className="mt-4 flex flex-wrap gap-2">
                {tags.map((tag) => (
                  <li key={tag.slug}>
                    <Link
                      href={`/tags/${encodeURIComponent(tag.slug)}`}
                      className="inline-block border border-line px-2.5 py-1 text-meta text-muted hover:border-accent hover:text-accent"
                    >
                      {tag.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-line">
        <div className="mx-auto max-w-[1240px] px-5 py-6 sm:px-8">
          <p className="text-note leading-[1.9] text-muted">
            本サイトの記事は、公開されている動画や公式発表・報道を生成AIが解析・要約して自動生成したものです（出典は各記事に記載）。誤りが含まれる可能性があるため、重要な判断の際は必ず出典および各社の公式発表をご確認ください。
            {hasActiveAffiliate() &&
              "当サイトは広告およびアフィリエイトプログラムによる収益を得ています。"}
          </p>
          <p className="index-num mt-4 text-meta text-muted">
            © {new Date().getFullYear()} {siteConfig.name}
          </p>
        </div>
      </div>
    </footer>
  );
}
