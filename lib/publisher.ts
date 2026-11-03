import { siteConfig } from "@/site.config";

/** 全ページで同じ媒体を指す。検索結果の favicon とは別の、発行元のロゴ情報。 */
export const publisherJsonLd = {
  "@type": "NewsMediaOrganization",
  "@id": `${siteConfig.url}/#publisher`,
  name: siteConfig.siteName,
  // 旧名「サイソクAIニュース」を残す（改名後も Google に同じ媒体だと伝えるため。消さない）
  alternateName: [siteConfig.formerName, siteConfig.nickname, "AI速報サイソク"],
  url: siteConfig.url,
  // Organization のロゴは縦横とも 112px 以上。既存のブランド画像を使う。
  logo: { "@type": "ImageObject", url: `${siteConfig.url}/icons/icon-512.png`, width: 512, height: 512 },
  email: siteConfig.operator.email,
  contactPoint: { "@type": "ContactPoint", email: siteConfig.operator.email, contactType: "editorial" },
  publishingPrinciples: `${siteConfig.url}/editorial-policy`,
  correctionsPolicy: `${siteConfig.url}/corrections`,
  sameAs: siteConfig.social.map((social) => social.url),
  // 運営責任者（所在地は都道府県まで）
  founder: { "@type": "Person", name: siteConfig.operator.representative },
  address: { "@type": "PostalAddress", addressRegion: siteConfig.operator.location, addressCountry: "JP" },
} as const;

/** 記事・ガイドの著者。全ページで同じ編集部を指し、発行元とつなぐ */
export const authorJsonLd = {
  "@type": "Organization",
  "@id": `${siteConfig.url}/#editorial`,
  name: siteConfig.author,
  url: `${siteConfig.url}/about`,
  parentOrganization: { "@id": `${siteConfig.url}/#publisher` },
} as const;
