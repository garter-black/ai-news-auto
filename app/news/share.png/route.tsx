import fs from "node:fs/promises";
import path from "node:path";
import { ImageResponse } from "next/og";

// 既存のロゴ画像と同じく、フォントを読めるビルド時に描画する。
export const dynamic = "force-static";

export async function GET() {
  const fontDir = path.join(process.cwd(), "assets", "fonts");
  const [dela, noto] = await Promise.all([
    fs.readFile(path.join(fontDir, "DelaGothicOne-Regular.ttf")),
    fs.readFile(path.join(fontDir, "NotoSansJP-Bold.otf")),
  ]);

  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", background: "#f3f5f8", color: "#0b1a33", fontFamily: "Noto", padding: "54px 68px", borderTop: "14px solid #c8221a" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontFamily: "Dela", fontSize: 48, color: "#1d4a8f" }}>サイソク</span>
          <span style={{ fontSize: 26, color: "#1d4a8f" }}>最速で、要点だけ。</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", marginTop: 54 }}>
          <span style={{ width: 12, height: 102, background: "#c8221a", marginRight: 26 }} />
          {/* 見出しは読みやすい Noto（ロゴ用の書体はロゴだけ。台帳 W1・2026-11-04 オーナー承認） */}
          <span style={{ fontFamily: "Noto", fontWeight: 700, fontSize: 96, lineHeight: 1.2 }}>ニュース</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", marginTop: 34, fontSize: 32, lineHeight: 1.65 }}>
          <span>ビジネス・テクノロジー・暮らし</span>
          <span>エンタメ・スポーツ・科学・社会</span>
        </div>
        <div style={{ display: "flex", marginTop: "auto", paddingTop: 25, borderTop: "2px solid #cbd3df", fontSize: 26, color: "#1d4a8f" }}>
          企業・研究機関・行政の公式発表から
        </div>
      </div>
    ),
    {
      width: 1200,
      height: 630,
      fonts: [
        { name: "Dela", data: dela, weight: 400, style: "normal" },
        { name: "Noto", data: noto, weight: 700, style: "normal" },
      ],
    },
  );
}
