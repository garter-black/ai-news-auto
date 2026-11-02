import { promises as fs } from "@/lib/content-fs";
import path from "node:path";
import { loadDefaultJapaneseParser } from "budoux";
import { ImageResponse } from "next/og";
import sharp from "sharp";
import { coverPath, getAllArticles, getArticle, PRERENDER_LIMIT, type FullArticle } from "@/lib/articles";
import { companyOf } from "@/lib/companies";
import { officialCreditText } from "@/lib/image-credit";
import { siteConfig } from "@/site.config";
import {
  basePhoto,
  COVER_HEIGHT as HEIGHT,
  COVER_WIDTH as WIDTH,
  fetchPhoto,
  toDataUrl,
  withBottomShade,
} from "@/lib/cover";

/**
 * 記事ごとのカバー画像（1200×630）。
 *
 * YouTube のサムネイルをそのまま使うと「よそのコンテンツを貼っただけ」に見えるので、
 * 日本語の見出し・発表元の企業・サイト名を載せた独自の画像を生成する。
 * 新しい記事だけビルド時に作り、残りは初回アクセス時に作ってキャッシュする（ISR）。
 * SNS（Threads・X）でシェアされたときのカードもこの画像になる。
 * 外部の画像生成APIは使わないので費用はかからない。
 *
 * デザインは報道テロップ型：写真は加工せず、下だけ自然に暗くして白い極太の見出しを載せる。
 * 左に発表元の企業色の縦線、見出しの上に赤い「速報」ラベル。
 */
export const dynamic = "force-static";
export const dynamicParams = true;
// 一度作ったカバーは1週間使い回す（デプロイごとに作り直される）
export const revalidate = 604800;

export function generateStaticParams() {
  return getAllArticles()
    .slice(0, PRERENDER_LIMIT)
    .filter((article) => coverPath(article))
    .map((article) => ({ slug: article.slug }));
}

const FONT_DIR = path.join(process.cwd(), "assets", "fonts");
const parser = loadDefaultJapaneseParser();

const MARGIN = 56;
const BAR = 8;
const BAR_GAP = 24;
const TEXT_WIDTH = WIDTH - MARGIN * 2 - BAR - BAR_GAP;
const SHADOW = "0 2px 14px rgba(0,0,0,0.35)";

let fonts: Promise<{ name: string; data: Buffer; weight: 400 | 700 | 900 }[]> | null = null;
function loadFonts() {
  fonts ??= Promise.all([
    fs.readFile(path.join(FONT_DIR, "NotoSansJP-Black.otf")),
    fs.readFile(path.join(FONT_DIR, "NotoSansJP-Bold.otf")),
    fs.readFile(path.join(FONT_DIR, "DelaGothicOne-Regular.ttf")),
  ]).then(([black, bold, dela]) => [
    { name: "Noto", data: black, weight: 900 as const },
    { name: "Noto", data: bold, weight: 700 as const },
    { name: "Dela", data: dela, weight: 400 as const },
  ]);
  return fonts;
}

/** 「製品名｜説明」の形なら、製品名を大見出し・説明を小見出しに分ける */
function splitTitle(title: string): { head: string; tail: string } | null {
  const match = /^(.{2,36}?)[｜：|:]\s*(.+)$/.exec(title);
  return match ? { head: match[1], tail: match[2] } : null;
}

/** 文字幅のおおよその合計（全角=1、半角=0.56） */
function emWidth(text: string): number {
  let total = 0;
  for (const char of text) total += /[\u0000-ÿ]/.test(char) ? 0.56 : 1;
  return total;
}

/** 文節ごとに分け、単語の途中で改行されないようにする */
function Phrases({
  text,
  size,
  weight,
  color,
}: {
  text: string;
  size: number;
  weight: 700 | 900;
  color: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        fontSize: size,
        fontWeight: weight,
        lineHeight: 1.3,
        color,
        textShadow: SHADOW,
      }}
    >
      {parser.parse(text).map((phrase, index) => (
        // 縮ませると文節の中で改行されてしまうので固定する
        <span key={index} style={{ display: "flex", flexShrink: 0 }}>
          {phrase}
        </span>
      ))}
    </div>
  );
}

// 写真入りの画像は PNG だと1枚 600KB 前後になるので、JPEG にして 1/5〜1/10 にする
// （ビルド時に作る分は Deployment Storage、実行時に作る分は Fast Origin Transfer を食う）。
// URL（/cover/<slug>）は変えないので、OGP タグや SNS のカードはそのまま
const JPEG_QUALITY = 82;
const CACHE_HEADERS = {
  "Content-Type": "image/jpeg",
  // 同じデプロイの間は内容が変わらない。CDN では7日、ブラウザでも7日使い回す
  "Cache-Control": "public, max-age=604800, s-maxage=604800, stale-while-revalidate=86400, immutable",
};

async function toJpeg(image: ArrayBuffer | Buffer): Promise<Uint8Array<ArrayBuffer>> {
  const jpeg = await sharp(Buffer.isBuffer(image) ? image : Buffer.from(image))
    .flatten({ background: "#111111" })
    .jpeg({ quality: JPEG_QUALITY, mozjpeg: true, chromaSubsampling: "4:2:0" })
    .toBuffer();
  return new Uint8Array(jpeg);
}

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article || !coverPath(article)) return new Response("Not found", { status: 404 });

  try {
    // 描画の失敗も捕まえるため、ここで最後まで描き切ってから JPEG にする
    const png = await (await renderCover(article)).arrayBuffer();
    const jpeg = await toJpeg(png).catch(() => null);
    // Cloudflare Workers（sharp なし）では PNG のまま返す
    if (!jpeg) return new Response(png, { headers: { ...CACHE_HEADERS, "Content-Type": "image/png" } });
    return new Response(jpeg, { headers: CACHE_HEADERS });
  } catch (error) {
    console.error(`[cover] fallback: ${slug}`, error);
    return fallbackCover(companyOf(article).color);
  }
}

/** 描画に失敗したときの単色カバー（500 にしない） */
async function fallbackCover(color: string): Promise<Response> {
  try {
    const jpeg = await sharp({
      create: { width: WIDTH, height: HEIGHT, channels: 3, background: /^#[0-9a-f]{3,8}$/i.test(color) ? color : "#1d1d22" },
    })
      .jpeg({ quality: JPEG_QUALITY })
      .toBuffer();
    return new Response(new Uint8Array(jpeg), { headers: { ...CACHE_HEADERS, "Cache-Control": "public, max-age=3600" } });
  } catch {
    return Response.redirect(new URL("/logo.png", siteConfig.url), 302);
  }
}

type CoverBackground = { src: string | null; photo: boolean; cssShade: boolean };

/**
 * 背景。通常は sharp で切り抜き・陰影を焼き込む。
 * Cloudflare Workers では sharp が使えないので、写真は URL のまま敷き、陰影は CSS で付ける
 */
async function coverBackground(url: string, color: string): Promise<CoverBackground> {
  try {
    const photo = await fetchPhoto(url);
    const base = await basePhoto(photo, color);
    return { src: await toDataUrl(await withBottomShade(base, 0.38, 0.88)), photo: Boolean(photo), cssShade: false };
  } catch {
    const src = await reachablePhoto(url);
    return { src, photo: Boolean(src), cssShade: true };
  }
}

/** 画像として取得できる URL（YouTube の高解像度版が無ければ標準画質） */
async function reachablePhoto(url: string): Promise<string | null> {
  if (!url || !/^https?:\/\//.test(url)) return null;
  const candidates = url.includes("maxresdefault") ? [url, url.replace("maxresdefault", "hqdefault")] : [url];
  for (const candidate of candidates) {
    try {
      const res = await fetch(candidate, { method: "HEAD", signal: AbortSignal.timeout(5000) });
      const type = res.headers.get("content-type") ?? "";
      // 描画エンジンが読める形式だけ
      if (res.ok && /image\/(jpeg|png)/.test(type)) return candidate;
    } catch {
      // 次の候補へ
    }
  }
  return null;
}

/** カバー左上の注記（フリー素材・AI 生成・公式発表の写真のとき。CC BY は作者とライセンスの表示が条件） */
function coverCredit(article: FullArticle): string {
  const c = article.imageCredit;
  if (c?.kind === "ai") return "画像はイメージ（AI生成）";
  if (c?.kind === "official" || c?.kind === "figure") return officialCreditText(c) ?? "";
  if (c?.kind !== "stock") return "";
  const who = (c.creator || "").slice(0, 28);
  return c.license?.startsWith("CC BY") ? `写真はイメージ　Photo: ${who} / ${c.license}` : "写真はイメージ";
}

async function renderCover(article: FullArticle): Promise<ImageResponse> {
  const company = companyOf(article);
  const isOther = company.slug === "other";
  // 公式ブログ・プレスリリース記事で企業が判別できないときは、発信元名を出す
  const sourceName = article.sourceType === "web" ? article.sourceName || article.channel : "";
  const label = article.type === "roundup" ? `${article.sources?.length ?? 0}本の発表を統合` : "速報";
  const title = article.seoTitle || article.title;
  const parts = splitTitle(title);

  const baseColor = isOther ? "#1d1d22" : company.color;
  const [bg, fontData] = await Promise.all([coverBackground(article.thumbnail, baseColor), loadFonts()]);
  const photo = bg.photo;
  const credit = coverCredit(article);

  // 製品名は1行に収める（長ければ小さくする）
  const headSize = parts ? Math.min(58, Math.floor(TEXT_WIDTH / Math.max(emWidth(parts.head), 1))) : 0;

  return new ImageResponse(
    (
      <div
        style={{
          width: WIDTH,
          height: HEIGHT,
          display: "flex",
          position: "relative",
          background: bg.src ? "#111" : baseColor,
          fontFamily: "Noto",
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        {bg.src && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={bg.src} alt="" width={WIDTH} height={HEIGHT} style={{ position: "absolute", left: 0, top: 0, objectFit: "cover" }} />
        )}
        {bg.cssShade && (
          <div
            style={{
              position: "absolute",
              left: 0,
              top: 0,
              width: WIDTH,
              height: HEIGHT,
              backgroundImage: "linear-gradient(to bottom, rgba(0,0,0,0) 38%, rgba(0,0,0,0.88) 100%)",
            }}
          />
        )}

        {/* 元動画が削除されて写真が無いときは、企業色の地に企業名を大きく敷く */}
        {!photo && (
          <div
            style={{
              position: "absolute",
              left: 48,
              top: 70,
              display: "flex",
              // 発信元名は長いことがあるので、横幅に収まる大きさにする
              fontSize: isOther && sourceName ? Math.min(200, Math.floor(1100 / Math.max(emWidth(sourceName), 1))) : 200,
              fontWeight: 900,
              letterSpacing: -4,
              color: "rgba(255,255,255,0.14)",
            }}
          >
            {isOther ? sourceName || "NEWS" : company.name}
          </div>
        )}

        {/* イメージ写真の注記 */}
        {photo && credit && (
          <div
            style={{
              position: "absolute",
              left: 48,
              top: 46,
              display: "flex",
              fontSize: 17,
              fontWeight: 700,
              color: "rgba(255,255,255,0.86)",
              textShadow: "0 1px 8px rgba(0,0,0,0.6)",
            }}
          >
            {credit}
          </div>
        )}

        {/* サイト名 */}
        <div
          style={{
            position: "absolute",
            right: 48,
            top: 40,
            display: "flex",
            fontFamily: "Dela",
            fontSize: 28,
            color: "#fff",
            textShadow: "0 2px 10px rgba(0,0,0,0.45)",
          }}
        >
          サイソクニュース
        </div>

        {/* 見出し：左に企業色の縦線、上に赤いラベル */}
        <div
          style={{
            position: "absolute",
            left: MARGIN,
            bottom: MARGIN,
            width: WIDTH - MARGIN * 2,
            display: "flex",
            gap: BAR_GAP,
          }}
        >
          <div style={{ width: BAR, borderRadius: BAR / 2, background: isOther ? "#d92d20" : company.color }} />
          <div style={{ width: TEXT_WIDTH, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 23, fontWeight: 700, color: "#fff" }}>
              <div style={{ display: "flex", padding: "3px 13px", borderRadius: 4, background: "#d92d20" }}>{label}</div>
              {(!isOther || sourceName) && (
                <div style={{ display: "flex", textShadow: SHADOW }}>{isOther ? sourceName : company.name}</div>
              )}
            </div>

            {parts ? (
              // 画像の描画エンジンは <></> を正しく縦に並べないので、明示的に縦並びの箱で包む
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div
                  style={{
                    display: "flex",
                    fontSize: headSize,
                    fontWeight: 900,
                    lineHeight: 1.25,
                    color: "#fff",
                    textShadow: SHADOW,
                  }}
                >
                  {parts.head}
                </div>
                <Phrases text={parts.tail} size={31} weight={700} color="rgba(255,255,255,0.9)" />
              </div>
            ) : (
              <Phrases text={title} size={emWidth(title) <= 18 ? 58 : 50} weight={900} color="#fff" />
            )}
          </div>
        </div>
      </div>
    ),
    {
      width: WIDTH,
      height: HEIGHT,
      fonts: fontData,
    },
  );
}
