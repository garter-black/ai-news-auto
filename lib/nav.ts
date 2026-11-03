import { getAllGenres } from "@/lib/articles";
import { broadSection } from "@/lib/sections";

export type NavItem = { href: string; label: string };

/**
 * ジャンルのタブの並び（読者像の順。AGENTS.md「0. 読者」・docs/persona-20261102.md）。
 * 速報 → AI → 仕事とお金（ビジネス・投資・不動産）→ 税・制度（社会・行政）→ ガジェット → スポーツ → 暮らし → エンタメ。
 * 「起業・副業」はジャンルのページが無い（記事も数本だけ）ので、ページができたらここに足す。
 * 科学・宇宙は読者像の外なので、タブには出さず /news の切り替えから辿る。
 */
export const GENRE_NAV_ORDER = [
  "ai",
  "business",
  "investment",
  "real-estate",
  "public",
  "tech-gadget",
  "sports",
  "lifestyle",
  "entertainment",
] as const;

/** ジャンル名。ジャンルのページ（/genres/<slug>）の見出しと同じ名前（記事の genre を優先し、無ければ幅広い話題の静的な名前） */
export function genreLabel(slug: string): string {
  return getAllGenres().find((g) => g.slug === slug)?.label ?? broadSection(slug)?.label ?? slug;
}

/** ヘッダーのジャンルのタブ。先頭は全ジャンルの新着（速報）。記事の無いジャンルはページが 404 になるので出さない */
export function genreNav(): NavItem[] {
  const live = new Set(getAllGenres().map((g) => g.slug));
  return [
    { href: "/articles", label: "速報" },
    ...GENRE_NAV_ORDER.filter((slug) => live.has(slug)).map((slug) => ({ href: `/genres/${slug}`, label: genreLabel(slug) })),
  ];
}

/** 何度も使う定番ページ（PC は帯の右、スマホはタブの最後） */
export const UTILITY_NAV: NavItem[] = [
  { href: "/guides", label: "使い方ガイド" },
  { href: "/ai-pricing", label: "AI料金表" },
  { href: "/tools", label: "計算ツール" },
  { href: "/products", label: "AIサービス図鑑" },
];
