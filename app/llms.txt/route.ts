import { getAllArticles } from "@/lib/articles";
import { COMPANIES, companyCounts } from "@/lib/companies";
import { getAllGuides } from "@/lib/guides";
import { getAllBuyingGuides } from "@/lib/buying-guides";
import { listedProducts, productHeading, supersedesGuide } from "@/lib/products";
import { STATUS_LABEL, verdictOf } from "@/lib/verdict";
import { siteConfig } from "@/site.config";

export const dynamic = "force-static";

/**
 * AI検索・AIアシスタント向けのサイト案内（llms.txt）。
 * どんなサイトで、どのページに何があるかを短い Markdown で伝え、AIの回答で引用されやすくする。
 */
export function GET() {
  const base = siteConfig.url;
  const ai = getAllArticles().filter((a) => a.genreSlug === "ai");
  const latest = ai.filter((a) => a.type !== "roundup").slice(0, 40);
  const roundups = ai.filter((a) => a.type === "roundup").slice(0, 10);
  const companies = companyCounts(ai).filter(({ company }) => COMPANIES.some((c) => c.slug === company.slug));

  const line = (title: string, url: string, note?: string) => `- [${title}](${url})${note ? `: ${note}` : ""}`;
  const products = listedProducts();
  // まとめページへ移した製品ガイドは転送しているので載せない
  const guides = getAllGuides().filter((g) => !supersedesGuide(g.slug));

  const text = `# ${siteConfig.name}（${siteConfig.tagline}）

> ${siteConfig.description}

${siteConfig.name}は、企業・官公庁の公式ブログ・ニュースリリース・発表を常時監視し、日本語の記事にする速報メディアです。公式の資料が無い話題は報道機関の記事を出典にし、その旨を記事に明記しています。
各記事には「使えるか」の判定（提供状況・日本から使えるか・料金・無料で試せるか・向いている人）を付けています。
判定は発表時点の情報にもとづき、記事内に判定日を明記しています。運営：${siteConfig.operator.name}（${siteConfig.operator.email}）

## 主なページ

${line("トップ（新着の時系列）", `${base}/`)}
${line("記事一覧", `${base}/articles`)}
${line("製品まとめ一覧", `${base}/products`, "AIモデル・アプリごとの「とは・料金・日本で使えるか・これまでの流れ・FAQ」（出典の記事つき）")}
${line("AIの料金・日本対応 早見表", `${base}/ai-pricing`, "API料金（100万トークンあたり）と月額プラン、日本で使えるか。数字は出典の記事で確認ずみ")}
${line("今週のAIニュースまとめ", `${base}/weekly`, "毎週月曜更新。1週間の重要な10本を2行ずつ")}
${line("AIイベントの日程", `${base}/events`, "主要イベントの日本時間の基調講演と、開催後の発表内容")}

${line("RSS", `${base}/feed.xml`)}

## 製品まとめ

${products.map((p) => line(productHeading(p), `${base}/products/${p.slug}`, p.overview.text)).join("\n")}
${guides.length > 0 ? `\n## 製品ガイド\n\n${guides.map((g) => line(g.title, `${base}/guides/${g.slug}`, g.description)).join("\n")}` : ""}

## 比較ガイド（公式情報と価格をもとに比較。広告を含む）



## 企業別の最新発表

${companies.map(({ company, count }) => line(`${company.name}の最新AIニュース`, `${base}/companies/${company.slug}`, `${count}本`)).join("\n")}

## 話題まとめ（複数の発表を1本に統合）

${roundups.map((a) => line(a.title, `${base}/articles/${a.slug}`, a.description)).join("\n")}

## 最新記事

${latest
  .map((a) => {
    const verdict = verdictOf(a);
    const note = verdict ? `【${STATUS_LABEL[verdict.status]}・${verdict.price}】${a.description}` : a.description;
    return line(a.title, `${base}/articles/${a.slug}`, note);
  })
  .join("\n")}
`;

  return new Response(text, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
