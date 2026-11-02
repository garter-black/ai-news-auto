/**
 * トップの「ストーリーの丸」に記事を入れておく時間。
 * サーバー（lib/stories.ts）とブラウザ（components/story/SwipeDeck.tsx）の両方で使うので、
 * fs などに触れない小さなファイルに分けてある。
 */
export const STORY_WINDOW_MS = 24 * 3_600_000;

/** 並び順：段（tier）が小さい丸から、同じ段の中は新しい記事（newest はミリ秒）がある丸から */
export function byTierThenNewest(x: { tier?: number; newest: number }, y: { tier?: number; newest: number }): number {
  return (x.tier ?? 2) - (y.tier ?? 2) || y.newest - x.newest;
}

/** 丸の判定に要る形（丸の一覧用の軽い束 RailBundle と、デッキ用の束 Bundle の両方で使う） */
type Windowed = { tier?: number; cover?: string; items: { at: string; image: string }[] };

const rank = (b: Windowed) => ({ tier: b.tier, newest: Date.parse(b.items[0].at) });
export const compareBundles = (x: Windowed, y: Windowed) => byTierThenNewest(rank(x), rank(y));

/** 今から24時間以内の記事だけを残し、0本の丸は消して、段ごとに新しい順で並べ直す */
export function liveBundles<B extends Windowed>(bundles: B[], now: number): B[] {
  return bundles
    .map((b) => {
      const items = b.items.filter((it) => now - Date.parse(it.at) < STORY_WINDOW_MS) as B["items"];
      const cover = items.some((it) => it.image === b.cover) ? b.cover : items.find((it) => it.image)?.image;
      return { ...b, cover, items };
    })
    .filter((b) => b.items.length > 0)
    .sort(compareBundles);
}
