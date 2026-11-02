import { DIGEST_DESIGN, DIGEST_DESIGNS, type DigestDesign } from "@/lib/digest-design";
import { getDigest } from "@/lib/digest";
import { renderDigestImage, type DigestFormat } from "@/lib/digest-image";

/**
 * /digest/<id>/share.png（1200×630）と /digest/<id>/card.png（1080×1350）の共通処理。
 * 号のデータが KV にしか無い（デプロイ前の）朝もあるので、静的化せずリクエスト時に描き、エッジ（cloudflare/worker.ts）に置く。
 * ?design=A|B|B1|B2|C で案を見比べられる（既定は lib/digest-design.ts の DIGEST_DESIGN）。
 */
type KvLike = { get(key: string, type: "arrayBuffer"): Promise<ArrayBuffer | null> };

async function ownImageReader(): Promise<((name: string) => Promise<ArrayBuffer | null>) | undefined> {
  if (!(globalThis as { __SAISOKU_VFS__?: unknown }).__SAISOKU_VFS__) return undefined;
  try {
    const { getCloudflareContext } = await import("@opennextjs/cloudflare");
    const { env } = await getCloudflareContext({ async: true });
    const kv = (env as unknown as { IMAGES_KV?: KvLike }).IMAGES_KV;
    return kv ? (name: string) => kv.get(name, "arrayBuffer") : undefined;
  } catch {
    return undefined;
  }
}

export async function digestImageResponse(request: Request, id: string, format: DigestFormat): Promise<Response> {
  const digest = await getDigest(id);
  if (!digest) return new Response("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  const asked = new URL(request.url).searchParams.get("design")?.toUpperCase();
  const design: DigestDesign = DIGEST_DESIGNS.find((d) => d === asked) ?? DIGEST_DESIGN;
  try {
    const image = await renderDigestImage(digest, design, format, await ownImageReader());
    const png = await image.arrayBuffer();
    return new Response(png, {
      headers: {
        "Content-Type": "image/png",
        // 号の中身は変わらない。ブラウザ1日・エッジ7日（デプロイで JS が変われば作り直す）
        "Cache-Control": "public, max-age=86400, s-maxage=604800",
      },
    });
  } catch (error) {
    console.error(`[digest] image failed: ${id}`, error);
    return new Response("Image unavailable", { status: 503, headers: { "Cache-Control": "no-store", "Retry-After": "30" } });
  }
}
