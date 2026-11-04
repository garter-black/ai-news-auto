/** 一覧ページの1ページあたりの件数。記事が何千本に増えてもページの重さを一定に保つ */
export const PER_PAGE = 60;

export type Paged<T> = { items: T[]; page: number; totalPages: number; total: number };

export function paginate<T>(items: T[], page: number, perPage = PER_PAGE): Paged<T> {
  const totalPages = Math.max(1, Math.ceil(items.length / perPage));
  return {
    items: items.slice((page - 1) * perPage, page * perPage),
    page,
    totalPages,
    total: items.length,
  };
}

/**
 * ビルド時に静的化する「2ページ目以降」の番号（generateStaticParams 用）。
 * デプロイ直後の初回アクセスでページを作らないよう、ある分はすべてビルドで作る（台帳 W29）
 */
export function laterPages(total: number, perPage = PER_PAGE): string[] {
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  return Array.from({ length: totalPages - 1 }, (_, i) => String(i + 2));
}

/** URL の「/page/2」の数字。2以上の整数だけ受け付ける（1ページ目は元のURL） */
export function parsePage(raw: string): number | null {
  return /^[1-9]\d*$/.test(raw) && Number(raw) >= 2 ? Number(raw) : null;
}

/** n ページ目の URL（1ページ目は base のまま） */
export function pageHref(base: string, page: number): string {
  return page <= 1 ? base : `${base}/page/${page}`;
}

/** タイトル用の「（2ページ目）」 */
export function pageSuffix(page: number): string {
  return page > 1 ? `（${page}ページ目）` : "";
}
