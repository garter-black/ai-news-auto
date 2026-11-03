import { getAllArticles, type ArticleSummary } from "@/lib/articles";

/**
 * AI以外の幅広い話題（scripts/genres.py の BROAD_GENRES と同じ slug ＋ 投資・マネー／不動産）。
 * トップの下段と /news にまとめて出す。投資・マネーと不動産は読者像の中心（AGENTS.md「0. 読者」）なので
 * 2026-11-03 に足した（それまではトップに「過去に扱っていた分野」と出ており、運営者情報の説明と食い違っていた）。
 * label は記事に名前が無いときの予備。表示は記事の genre を優先する。
 */
export const BROAD_SECTIONS: { slug: string; label: string; description: string }[] = [
  {
    slug: "business",
    label: "ビジネス・経済",
    description:
      "企業のプレスリリースや日本銀行・各種統計の発表を一次情報から読み解き、業界の動きや景気の数字が働く人と家計にどう関わるかを解説します。",
  },
  {
    slug: "investment",
    label: "投資・マネー",
    description:
      "証券・銀行・日本銀行・金融庁などの公式発表をもとに、NISA・金利・為替・家計にかかわる新しい動きを、自分のお金にどう関係するかまで整理します。特定の銘柄の売買はすすめません。",
  },
  {
    slug: "real-estate",
    label: "不動産",
    description:
      "国や自治体、不動産会社・住宅メーカーの公式発表をもとに、地価・住宅ローン・家賃・再開発など住まいと資産にかかわる動きを、数字と時期を中心にまとめます。特定の物件の売買はすすめません。",
  },
  {
    slug: "tech-gadget",
    label: "テクノロジー・ガジェット",
    description:
      "スマホ・PC・家電・通信サービスなどの新製品と新サービスを、メーカーの公式発表をもとに、価格・発売日・何が変わったかを中心にまとめます。",
  },
  {
    slug: "lifestyle",
    label: "暮らし・生活",
    description:
      "食品・日用品・交通・住まいなど、毎日の暮らしに関わる新商品や値上げ、サービス変更の発表を、いつから何が変わるかに絞って伝えます。",
  },
  {
    slug: "entertainment",
    label: "エンタメ・カルチャー",
    description:
      "映画・音楽・ゲーム・アニメ・イベントの公式発表から、公開日・発売日・参加方法など、楽しむために必要な情報を整理して届けます。",
  },
  {
    slug: "sports",
    label: "スポーツ",
    description:
      "競技団体や大会・チームの公式発表をもとに、大会の日程や結果、参加・観戦の方法など、押さえておきたい情報をまとめます。",
  },
  {
    slug: "science",
    label: "科学・宇宙",
    description:
      "大学・研究機関のプレスリリースを一次情報から読み解き、新しい研究成果が何を明らかにしたのか、暮らしや社会にどうつながるのかをわかりやすく解説します。",
  },
  {
    slug: "public",
    label: "社会・行政",
    description:
      "国や自治体・公的機関の発表から、制度の変更、給付・手続き、防災情報など、生活に関わる決まりごとを、対象者と期限を中心に整理します。",
  },
];

const BROAD_SLUGS = new Set(BROAD_SECTIONS.map((s) => s.slug));

export function isBroadGenre(slug: string): boolean {
  return BROAD_SLUGS.has(slug);
}

export function broadSection(slug: string) {
  return BROAD_SECTIONS.find((s) => s.slug === slug);
}

/** 幅広い話題の記事（新しい順） */
export function getBroadArticles(): ArticleSummary[] {
  return getAllArticles().filter((a) => isBroadGenre(a.genreSlug));
}

/** 記事がある幅広い話題だけを、BROAD_SECTIONS の並び順で（ラベルは記事の genre を優先） */
export function broadGenresWithArticles(articles: ArticleSummary[] = getBroadArticles()) {
  return BROAD_SECTIONS.map((section) => {
    const items = articles.filter((a) => a.genreSlug === section.slug);
    return { slug: section.slug, label: items[0]?.genre || section.label, count: items.length };
  }).filter((g) => g.count > 0);
}
