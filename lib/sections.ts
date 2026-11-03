import { getAllArticles, type ArticleSummary } from "@/lib/articles";

/**
 * AI以外の幅広い話題（scripts/genres.py の BROAD_GENRES と同じ slug ＋ 投資・マネー／不動産）。
 * トップの下段と /news にまとめて出す。投資・マネーと不動産は読者像の中心（AGENTS.md「0. 読者」）なので
 * 2026-11-03 に足した（それまではトップに「過去に扱っていた分野」と出ており、運営者情報の説明と食い違っていた）。
 * label は記事に名前が無いときの予備。表示は記事の genre を優先する。
 * 投資・マネー／不動産／エンタメの説明は、記事を作る側の決まり（scripts/genre_focus.py・genres.py。2026-11-03 オーナー決定）と同じ中身にする。
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
      "金融庁・日本銀行・証券取引所・証券会社・銀行の公式発表と大手企業の決算をもとに、NISA・iDeCo・税・手数料・金利の変更や市場を動かすニュースを、数字と時期を中心に整理します。特定の銘柄の売買はすすめず、取引のやり方も解説しません。",
  },
  {
    slug: "real-estate",
    label: "不動産",
    description:
      "国土交通省などの公的な資料・政策、地価公示・路線価、住宅ローン金利の改定、住宅の税制、不動産テック・AI 活用、大手デベロッパーや REIT のニュースを、数字と時期を中心にまとめます。特定の物件の売買はすすめません。",
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
      "配信サービスの料金・プラン、ゲーム業界の決算と新作、エンタメでのAI活用、アニメ・音楽の産業の数字、大きなプラットフォームの規約変更など、エンタメをビジネスとテクノロジーの目線で伝えます。",
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
