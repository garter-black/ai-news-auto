import type { Metadata } from "next";
import Link from "next/link";
import LegalPage from "@/components/LegalPage";
import corrections from "@/content/corrections.json";
import { siteConfig } from "@/site.config";

export const metadata: Metadata = {
  title: "訂正履歴",
  description: `${siteConfig.siteName}が公開後に訂正した記事の一覧です。いつ・何を直したかを公開しています。`,
  alternates: { canonical: "/corrections" },
};

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
      <ul>
        {items.map((c) => (
          <li key={`${c.date}-${c.slug}`}>
            <time dateTime={c.date}>{c.date}</time>{" "}
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
