import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import LegalPage from "@/components/LegalPage";
import corrections from "@/content/corrections.json";
import { siteConfig } from "@/site.config";

export const metadata: Metadata = withPageShare("corrections", "/corrections", {
  title: "訂正履歴・訂正の方針",
  description: `${siteConfig.siteName}の訂正の方針と、公開後に訂正した記事の一覧です。いつ・何を直したかを公開しています。`,
  alternates: { canonical: "/corrections" },
});

type Correction = { date: string; slug: string; title: string; summary: string };

/** 訂正履歴（新しい順）。記事を直したら content/corrections.json に1件足す */
export default function CorrectionsPage() {
  const items = [...(corrections as Correction[])].sort((a, b) => b.date.localeCompare(a.date));
  const latest = items[0]?.date.replace(/^(\d+)-0?(\d+)-0?(\d+)$/, "$1年$2月$3日") ?? "";
  return (
    <LegalPage
      eyebrow="CORRECTIONS"
      title="訂正履歴"
      lead="公開後に誤りが見つかった記事は、本文に訂正の日付と内容を書いたうえで、ここにも記録します。誤りを隠さず、何を直したかを残すための一覧です。"
      updatedAt={latest}
    >
      <h2>訂正の方針</h2>
      <ol>
        <li>
          <strong>受け付け：</strong>読者・発表元・権利者からのご指摘は、
          <Link href="/contact">お問い合わせ</Link>
          （メール）で受け付けます。編集部が自分で気づいた誤りも同じ扱いにします。
        </li>
        <li>
          <strong>確認：</strong>元の発表・公式資料と照らし合わせ、誤りかどうかを確かめます。ご指摘には、対応するかどうかにかかわらず必ず目を通します。
        </li>
        <li>
          <strong>本文の訂正：</strong>誤りだった場合は本文を直し、記事の冒頭または末尾に「訂正しました」という表示と、直した日付・内容を書きます。誤りの部分を黙って書き換えることはしません。
        </li>
        <li>
          <strong>一覧への記録：</strong>訂正した記事は、このページに日付と内容を残します。
        </li>
        <li>
          <strong>取り下げ：</strong>元の発表が撤回された場合や、内容の大部分が誤りだった場合は、記事を取り下げることがあります。
        </li>
      </ol>
      <p>
        誤字や表記ゆれなど、意味の変わらない小さな修正は、この一覧に載せないことがあります。
      </p>

      <h2>これまでの訂正</h2>
      <ul>
        {items.map((c) => (
          <li key={`${c.date}-${c.slug}`}>
            <time dateTime={c.date}>{c.date.replaceAll("-", "/")}</time>{" "}
            <Link href={`/articles/${c.slug}`}>{c.title}</Link>
            <br />
            {c.summary}
          </li>
        ))}
      </ul>
      <p>
        誤りに気づいた方は <a href={`mailto:${siteConfig.operator.email}`}>{siteConfig.operator.email}</a> または
        <Link href="/contact">お問い合わせ</Link>からお知らせください。訂正の方針は
        <Link href="/editorial-policy">編集方針</Link>に書いています。
      </p>
    </LegalPage>
  );
}
