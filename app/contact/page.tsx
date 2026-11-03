import type { Metadata } from "next";
import LegalPage from "@/components/LegalPage";
import { siteConfig } from "@/site.config";

export const metadata: Metadata = {
  title: "お問い合わせ",
  description: `${siteConfig.name}への記事内容の訂正依頼、権利者からの削除依頼、取材・掲載に関するお問い合わせ先です。`,
  alternates: { canonical: "/contact" },
};

export default function ContactPage() {
  const { email } = siteConfig.operator;

  return (
    <LegalPage
      eyebrow="CONTACT"
      title="お問い合わせ"
      lead="記事の誤りのご指摘、権利者の方からの削除依頼、その他のご連絡はこちらへお願いします。いただいたご連絡には順次対応します。"
      updatedAt="2026年11月3日"
    >
      <h2>連絡先</h2>
      <p>
        下記のメールアドレスまでご連絡ください。
        <br />
        <a href={`mailto:${email}`} className="text-lg font-bold">
          {email}
        </a>
      </p>

      <h2>ご連絡いただく際のお願い</h2>
      <p>
        確認を早めるため、お手数ですが以下をご記載ください。
      </p>
      <ul>
        <li>該当記事のURL</li>
        <li>該当箇所（見出し名や引用文）</li>
        <li>ご指摘の内容、または正しい情報</li>
      </ul>

      <h2>記事内容の訂正依頼</h2>
      <p>
        当サイトの記事は生成AIが下書きし、自動の事実確認を経て公開しています。すべての記事を人が読んでいるわけではないため、誤りが含まれる可能性があります。事実と異なる記述を見つけられた場合は、ぜひお知らせください。確認のうえ、訂正または記事の取り下げを行います。
      </p>

      <h2>権利者の方からの削除依頼</h2>
      <p>
        当サイトは、公式発表・動画などの要約・解説記事と、出典を示すための写真（公式発表の写真）・サムネイル・リンクを掲載しています。権利者の方で掲載に問題があると判断された場合は、当該記事のURLを添えてご連絡ください。権利者であることを確認できしだい、速やかに削除いたします。
        <strong>削除依頼は他のお問い合わせに優先して対応します。</strong>
      </p>

      <h2>取り上げの追加・除外のご希望</h2>
      <p>
        企業・団体の広報の方や動画チャンネルの運営者の方で、当サイトでの取り上げを希望される場合、あるいは取り上げないでほしい場合も、こちらからご連絡ください。監視対象からの除外に対応します。
      </p>

      <h2>ご返信について</h2>
      <p>
        個人で運営しているため、すべてのご連絡に返信できない場合があります。訂正依頼・削除依頼については、対応の可否にかかわらず必ず確認しています。
      </p>
    </LegalPage>
  );
}
