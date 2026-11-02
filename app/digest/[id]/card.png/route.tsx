import { digestImageResponse } from "@/lib/digest-route";

/** 朝のまとめの縦長画像（1080×1350。Threads・X の投稿に添える） */
export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return digestImageResponse(request, id, "tall");
}
