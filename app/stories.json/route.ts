import { getStoryBundles } from "@/lib/stories";

/**
 * トップの「ストーリーの丸」を開いたときに読むデッキの中身（要約・要点など）。
 * トップ（app/page.tsx）と同じくデプロイ時に作る静的なファイル。トップの HTML には件数・題名・表紙だけを載せる
 * （components/story/SwipeDeck.tsx の RailBundle）
 */
export const dynamic = "force-static";

export function GET() {
  // トップの HTML はエッジに前の版が残ることがある（cloudflare/worker.ts）。前の版の丸の記事も中身が出せるよう、
  // 24時間前の時点の束も足して48時間分を返す（ブラウザは記事の slug で引く）
  const now = Date.now();
  return Response.json([...getStoryBundles(now), ...getStoryBundles(now - 24 * 3_600_000)], {
    headers: { "Cache-Control": "public, max-age=300, s-maxage=86400" },
  });
}
