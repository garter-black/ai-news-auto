import { articleTitle } from "@/lib/headline";
import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import { Suspense } from "react";
import NewsletterSignup from "@/components/NewsletterSignup";
import NewsletterStatus from "@/components/NewsletterStatus";
import PushOptIn from "@/components/PushOptIn";
import { PUSH_RULES } from "@/lib/push-config";
import { getAllArticles } from "@/lib/articles";
import { isDistributionEligible } from "@/lib/indexing";
import { siteConfig } from "@/site.config";

export const metadata: Metadata = withPageShare("newsletter", "/newsletter", {
  title: "メール配信（毎朝7時のAIニュース要点）",
  description: `${siteConfig.siteName}のメール配信。毎朝7時に、前日のAIニュースから大きな発表を5本選び、1本2行の要点とリンクでお届けします。無料・いつでも解除できます。`,
  alternates: { canonical: "/newsletter" },
});

// サイト共通の Noto Sans JP（以前は細い Zen Kaku を避けるため OS の書体にしていた。游ゴシックは細いので使わない）
const FONT = "var(--font-sans)";

const POINTS = [
  { head: "毎朝7時に1通", body: "前日（24時間）に公開した記事から選びます。通勤前に読み終わる分量です。" },
  { head: "大きな発表から5本", body: "主要企業の新モデル・新機能の公式発表を優先し、話題の記事で埋めます。" },
  { head: "1本2行の要点", body: "見出しと2行の要点、記事へのリンクだけ。広告を挟んだ長いメールにはしません。" },
  { head: "いつでも解除", body: "すべてのメールの末尾に解除リンクがあります。押すだけで、翌日から届きません。" },
];

export default function NewsletterPage() {
  // 配信の見本（実際の配信は scripts/newsletter.py が前日分から選ぶ）
  const sample = getAllArticles()
    .filter((a) => a.genreSlug === "ai" && isDistributionEligible(a) && a.highlights.length > 0)
    .slice(0, 3);

  return (
    <div className="mx-auto max-w-3xl px-5 py-14 sm:px-8" style={{ fontFamily: FONT }}>
      <p className="eyebrow">NEWSLETTER</p>
      <h1 className="t-h1 mt-2">
        毎朝7時、昨日のAIニュースの<span className="whitespace-nowrap">要点を5本</span>
      </h1>
      <p className="mt-4 text-desc leading-[1.9] text-ink-soft">
        {siteConfig.siteName}のメール配信です。サイトを毎日のぞかなくても、前日に出た大事な発表だけを朝のうちに押さえられます。
      </p>

      <Suspense fallback={null}>
        <NewsletterStatus />
      </Suspense>

      <div className="mt-8">
        <NewsletterSignup variant="page" />
      </div>

      <h2 className="t-h2 mt-12">届く内容</h2>
      <dl className="mt-4 grid gap-x-8 gap-y-5 sm:grid-cols-2">
        {POINTS.map((p) => (
          <div key={p.head}>
            <dt className="text-note font-bold text-ink">{p.head}</dt>
            <dd className="mt-1 text-desc leading-[1.8] text-ink-soft">{p.body}</dd>
          </div>
        ))}
      </dl>

      {sample.length > 0 && (
        <>
          <h2 className="t-h2 mt-12">見本</h2>
          <p className="mt-1 text-meta text-muted">最近の記事で組んだ場合の例です（実際は5本）。</p>
          <ol className="mt-4 divide-y divide-line border-y border-line">
            {sample.map((a, i) => (
              <li key={a.slug} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-3 py-4">
                <span className="index-num pt-0.5 text-meta text-muted">{i + 1}</span>
                <div>
                  <Link href={`/articles/${a.slug}`} className="text-[15px] font-bold leading-snug text-ink hover:text-accent">
                    {articleTitle(a)}
                  </Link>
                  <p className="mt-1.5 line-clamp-2 text-desc leading-[1.75] text-ink-soft">
                    {a.highlights.slice(0, 2).join(" ")}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </>
      )}

      <h2 id="push" className="t-h2 mt-12">速報はプッシュ通知でも</h2>
      <p className="mt-3 text-desc leading-[1.8] text-ink-soft">
        主要企業の大きな発表があったとき、スマホやパソコンに通知でお知らせします。メールアドレスは要りません。
        {`1日${PUSH_RULES.perDay}回まで、夜${PUSH_RULES.quietFrom}時〜朝${PUSH_RULES.quietUntil}時は鳴らしません（夜の速報は朝${PUSH_RULES.quietUntil}時に1本だけ）。`}
      </p>
      <PushOptIn variant="page" />

      <h2 className="t-h2 mt-12">登録と解除について</h2>
      <ul className="mt-3 list-disc space-y-1.5 pl-5 text-desc leading-[1.8] text-ink-soft">
        <li>入力したアドレスに確認メールが届きます。メール内のリンクを開いた時点で登録が完了します（他人のアドレスで勝手に登録されることを防ぐため）。</li>
        <li>アドレスは配信と解除の管理にだけ使い、第三者に提供しません。詳しくは<Link href="/privacy" className="underline underline-offset-2">プライバシーポリシー</Link>をご覧ください。</li>
        <li>解除は各メール末尾のリンクから。ご不明な点は<Link href="/contact" className="underline underline-offset-2">お問い合わせ</Link>へ。</li>
      </ul>
    </div>
  );
}
