import fs from "node:fs/promises";
import path from "node:path";
import { ImageResponse } from "next/og";

/**
 * 横長のロゴ画像（白地に「サイソク」＋赤の「ニュース」）。ニュースアプリのフィードが求める大きさで作る。
 * 使うルートは force-static にして、ビルド時に一度だけ描く（Workers ではフォントを fs で読めないため）
 */
export async function wordmark(width: number, height: number): Promise<ImageResponse> {
  const font = await fs.readFile(path.join(process.cwd(), "assets", "fonts", "DelaGothicOne-Regular.ttf"));
  const size = Math.round(height * 0.6);
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: "#ffffff" }}>
        <span style={{ fontFamily: "Dela", fontSize: size, color: "#111111", letterSpacing: -1 }}>サイソク</span>
        <span style={{ fontFamily: "Dela", fontSize: Math.round(size * 0.6), color: "#d92d20", marginLeft: Math.round(size * 0.2) }}>ニュース</span>
      </div>
    ),
    { width, height, fonts: [{ name: "Dela", data: font, weight: 400, style: "normal" }] },
  );
}
