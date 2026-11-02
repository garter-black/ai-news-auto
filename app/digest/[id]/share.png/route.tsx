import { digestImageResponse } from "@/lib/digest-route";

/** 朝のまとめのシェア画像（1200×630。og:image・Bluesky のリンクカード） */
export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return digestImageResponse(request, id, "wide");
}
