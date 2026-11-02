import { promises as fs } from "@/lib/content-fs";
import path from "node:path";
import { loadDefaultJapaneseParser } from "budoux";
import { ImageResponse } from "next/og";
import sharp from "sharp";
import { getAllArticles, getArticle, type FullArticle } from "@/lib/articles";
import { companyOf } from "@/lib/companies";
import { fetchPhoto } from "@/lib/cover";
import { renderCardDesign, type CardDesign } from "@/lib/card-designs";

/**
 * どのカードを出すか。"tall" は今の縦長（見出し＋要点3つ）。
 * 2026-11 の新案は lib/card-designs.tsx（"a" 16:9 写真＋見出し帯 / "b" 1:1 / "c" 16:9 見出し主役）。切り替えはこの1行だけ。
 * 画像の URL（/card/<slug>）は1週間キャッシュされるので、変えると新しく出す記事から効く。
 */
const CARD_DESIGN: CardDesign | "tall" = "tall";

/**
 * SNS 投稿に添える図解カード（1080×1350・縦4:5）。
 *
 * 投稿だけで中身が分かるよう、上に元記事の写真（加工せず下だけ暗くする）、下に見出しと「3秒でわかる要点」3つを大きく載せる。
 * Buffer がこの URL から画像を取りに来る（scripts/threads_post.py の card_url）。
 * 作り方はカバー画像（app/cover/[slug]/route.tsx）と同じ：新しい記事だけビルド時に作り、残りは初回アクセス時に作ってキャッシュする。
 * 書体は Noto Sans JP（Black/Bold）。ロゴだけ Dela Gothic One。黄色のマーカーは使わない。
 */
export const dynamic = "force-static";
export const dynamicParams = true;
export const revalidate = 604800;

const WIDTH = 1080;
const HEIGHT = 1350;
const PHOTO_HEIGHT = 470;
const MARGIN = 64;
const BAR = 8;
const BAR_GAP = 22;
const BULLET = 48;
const BULLET_GAP = 20;
const PANEL = "#101014";
const RED = "#d92d20";
const HEAD_WIDTH = WIDTH - MARGIN * 2 - BAR - BAR_GAP;
const POINT_WIDTH = WIDTH - MARGIN * 2 - BULLET - BULLET_GAP;
// 見出しと要点に使える高さ（上の余白・ラベルの行・フッターを除く）
const LABEL_ROW = 44 + 22;
const TEXT_HEIGHT = HEIGHT - PHOTO_HEIGHT - 20 - LABEL_ROW - 96 - 16;
const HEAD_GAP = 34;
const POINT_GAP = 26;

// SNS に流すのは新しい記事なので、ビルド時に作るのは直近の分だけ（残りは初回アクセス時）
const CARD_PRERENDER = 40;

export function generateStaticParams() {
  return getAllArticles()
    .slice(0, CARD_PRERENDER)
    .map((article) => ({ slug: article.slug }));
}

const FONT_DIR = path.join(process.cwd(), "assets", "fonts");
const parser = loadDefaultJapaneseParser();

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

/** 文字幅のおおよその合計（全角=1、半角=0.56） */
function emWidth(text: string): number {
  let total = 0;
  for (const char of text) total += /[\u0000-ÿ]/.test(char) ? 0.56 : 1;
  return total;
}

/** 文節で折り返したときのおおよその行数（文節の切れ目で行末が余る分を多めに見込む） */
function lineCount(text: string, size: number, width: number): number {
  return Math.max(1, Math.ceil((emWidth(text) * 1.22) / (width / size)));
}

const HEAD_LH = 1.28;
const POINT_LH = 1.45;

/** 見出しと要点が収まる、いちばん大きい文字サイズ */
function fitSizes(title: string, points: string[]): { head: number; point: number } {
  for (let scale = 1; scale >= 0.6; scale -= 0.05) {
    const head = Math.round(60 * scale);
    const point = Math.round(40 * scale);
    const height =
      lineCount(title, head, HEAD_WIDTH) * head * HEAD_LH +
      HEAD_GAP +
      points.reduce((sum, p) => sum + lineCount(p, point, POINT_WIDTH) * point * POINT_LH, 0) +
      POINT_GAP * Math.max(points.length - 1, 0);
    if (height <= TEXT_HEIGHT) return { head, point };
  }
  return { head: 36, point: 24 };
}

/** 要点3つ（無ければリード文の文から補う）。文末の「。」は落とす */
function keyPoints(article: FullArticle): string[] {
  const clean = (s: string) => s.replace(/[✅👇]/gu, "").trim().replace(/。$/, "");
  const points = (article.highlights ?? []).map(clean).filter(Boolean);
  if (points.length < 3) {
    for (const sentence of (article.lede ?? "").split(/(?<=。)/)) {
      const s = clean(sentence);
      if (s && s.length <= 70 && !points.includes(s)) points.push(s);
      if (points.length >= 3) break;
    }
  }
  return points.slice(0, 3);
}

/** 文節ごとに分け、単語の途中で改行されないようにする */
function Phrases({ text, size, weight, color, lineHeight }: { text: string; size: number; weight: 700 | 900; color: string; lineHeight: number }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", fontSize: size, fontWeight: weight, lineHeight, color }}>
      {parser.parse(text).map((phrase, index) => (
        <span key={index} style={{ display: "flex", flexShrink: 0 }}>
          {phrase}
        </span>
      ))}
    </div>
  );
}

const JPEG_QUALITY = 84;
const CACHE_HEADERS = {
  "Content-Type": "image/jpeg",
  "Cache-Control": "public, max-age=604800, s-maxage=604800, stale-while-revalidate=86400, immutable",
};

async function toJpeg(image: ArrayBuffer): Promise<Uint8Array<ArrayBuffer>> {
  const jpeg = await sharp(Buffer.from(image))
    .flatten({ background: CARD_DESIGN === "tall" ? PANEL : "#ffffff" })
    .jpeg({ quality: JPEG_QUALITY, mozjpeg: true, chromaSubsampling: "4:2:0" })
    .toBuffer();
  return new Uint8Array(jpeg);
}

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const article = getArticle(slug);
  if (!article) return new Response("Not found", { status: 404 });
  try {
    const png = await (CARD_DESIGN === "tall" ? await renderCard(article) : await renderCardDesign(article, CARD_DESIGN)).arrayBuffer();
    const jpeg = await toJpeg(png).catch(() => null);
    // Cloudflare Workers（sharp なし）では PNG のまま返す
    if (!jpeg) return new Response(png, { headers: { ...CACHE_HEADERS, "Content-Type": "image/png" } });
    return new Response(jpeg, { headers: CACHE_HEADERS });
  } catch (error) {
    // 図解が描けないときはカバー画像に回す（投稿の画像が欠けないように）
    console.error(`[card] fallback: ${slug}`, error);
    return Response.redirect(new URL(`/cover/${slug}`, _request.url), 302);
  }
}

type Background = { src: string | null; photo: boolean; cssShade: boolean };

/** 上の写真。sharp があれば切り抜いて下だけ暗くし、Workers では写真の URL をそのまま敷いて CSS で暗くする */
async function photoBackground(url: string, color: string): Promise<Background> {
  try {
    const photo = await fetchPhoto(url);
    const base = photo
      ? await sharp(photo).resize(WIDTH, PHOTO_HEIGHT, { fit: "cover", position: sharp.strategy.attention }).toBuffer()
      : await sharp({ create: { width: WIDTH, height: PHOTO_HEIGHT, channels: 3, background: color } }).png().toBuffer();
    const shade = Buffer.from(
      `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${PHOTO_HEIGHT}"><defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">` +
        `<stop offset="0.45" stop-color="${PANEL}" stop-opacity="0"/><stop offset="1" stop-color="${PANEL}" stop-opacity="1"/>` +
        `</linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/></svg>`,
    );
    const jpeg = await sharp(base).composite([{ input: shade }]).jpeg({ quality: 86 }).toBuffer();
    return { src: `data:image/jpeg;base64,${jpeg.toString("base64")}`, photo: Boolean(photo), cssShade: false };
  } catch {
    const src = await reachablePhoto(url);
    return { src, photo: Boolean(src), cssShade: true };
  }
}

async function reachablePhoto(url: string): Promise<string | null> {
  if (!url || !/^https?:\/\//.test(url)) return null;
  const candidates = url.includes("maxresdefault") ? [url, url.replace("maxresdefault", "hqdefault")] : [url];
  for (const candidate of candidates) {
    try {
      const res = await fetch(candidate, { method: "HEAD", signal: AbortSignal.timeout(5000) });
      if (res.ok && /image\/(jpeg|png)/.test(res.headers.get("content-type") ?? "")) return candidate;
    } catch {
      // 次の候補へ
    }
  }
  return null;
}

function labelOf(article: FullArticle): string {
  if (article.type === "roundup") return "まとめ";
  if (article.breaking) return "速報";
  return "ニュース";
}

function dateOf(article: FullArticle): string {
  const d = new Date(article.publishedAt || article.generatedAt);
  if (Number.isNaN(d.getTime())) return "";
  const jst = new Date(d.getTime() + 9 * 3600 * 1000);
  return `${jst.getUTCFullYear()}.${jst.getUTCMonth() + 1}.${jst.getUTCDate()}`;
}

async function renderCard(article: FullArticle): Promise<ImageResponse> {
  const company = companyOf(article);
  const isOther = company.slug === "other";
  const sourceName = article.sourceType === "web" ? article.sourceName || article.channel : "";
  const accent = isOther ? RED : company.color;
  const baseColor = isOther ? "#1d1d22" : company.color;
  const title = article.title || article.seoTitle || "";
  const points = keyPoints(article);
  const size = fitSizes(title, points);
  const [bg, fontData] = await Promise.all([photoBackground(article.thumbnail, baseColor), loadFonts()]);

  return new ImageResponse(
    (
      <div style={{ width: WIDTH, height: HEIGHT, display: "flex", flexDirection: "column", background: PANEL, fontFamily: "Noto" }}>
        {/* 上：写真（無ければ企業色の地に企業名） */}
        <div style={{ width: WIDTH, height: PHOTO_HEIGHT, display: "flex", position: "relative", background: bg.src ? PANEL : baseColor }}>
          {bg.src && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={bg.src} alt="" width={WIDTH} height={PHOTO_HEIGHT} style={{ position: "absolute", left: 0, top: 0, objectFit: "cover" }} />
          )}
          {bg.cssShade && (
            <div
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                width: WIDTH,
                height: PHOTO_HEIGHT,
                backgroundImage: `linear-gradient(to bottom, rgba(16,16,20,0) 45%, ${PANEL} 100%)`,
              }}
            />
          )}
          {!bg.photo && (
            <div
              style={{
                position: "absolute",
                left: 52,
                top: 96,
                display: "flex",
                fontSize: isOther && sourceName ? Math.min(180, Math.floor(980 / Math.max(emWidth(sourceName), 1))) : 180,
                fontWeight: 900,
                letterSpacing: -4,
                color: "rgba(255,255,255,0.14)",
              }}
            >
              {isOther ? sourceName || "NEWS" : company.name}
            </div>
          )}
          {/* ロゴ */}
          <div style={{ position: "absolute", right: 52, top: 44, display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ display: "flex", fontFamily: "Dela", fontSize: 40, color: "#fff", textShadow: "0 2px 6px rgba(0,0,0,0.55), 0 0 18px rgba(0,0,0,0.45)" }}>サイソクニュース</div>
            <div style={{ width: 14, height: 14, borderRadius: 7, background: RED, marginTop: -18 }} />
          </div>
        </div>

        {/* 下：見出しと要点3つ */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: `20px ${MARGIN}px 0`, overflow: "hidden" }}>
          {/* ラベルと発表元（写真の上に載せると明るい写真で読めないので、地の上に置く） */}
          <div style={{ height: 44, marginBottom: 22, display: "flex", alignItems: "center", gap: 16, fontSize: 28, fontWeight: 700, color: "#fff" }}>
            <div style={{ display: "flex", padding: "2px 16px", borderRadius: 5, background: RED }}>{labelOf(article)}</div>
            {(!isOther || sourceName) && (
              <div style={{ display: "flex", color: "rgba(255,255,255,0.75)" }}>{isOther ? sourceName : company.name}</div>
            )}
          </div>
          <div style={{ display: "flex", gap: BAR_GAP }}>
            <div style={{ width: BAR, borderRadius: BAR / 2, background: accent }} />
            <div style={{ width: HEAD_WIDTH, display: "flex" }}>
              <Phrases text={title} size={size.head} weight={900} color="#ffffff" lineHeight={HEAD_LH} />
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: POINT_GAP, marginTop: HEAD_GAP }}>
            {points.map((point, index) => (
              <div key={index} style={{ display: "flex", gap: BULLET_GAP }}>
                <div
                  style={{
                    width: BULLET,
                    height: BULLET,
                    flexShrink: 0,
                    borderRadius: BULLET / 2,
                    background: accent,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 28,
                    fontWeight: 900,
                    color: "#fff",
                    // 1行目の高さに合わせる
                    marginTop: Math.max(0, (size.point * POINT_LH - BULLET) / 2),
                  }}
                >
                  {index + 1}
                </div>
                <div style={{ width: POINT_WIDTH, display: "flex" }}>
                  <Phrases text={point} size={size.point} weight={700} color="rgba(255,255,255,0.92)" lineHeight={POINT_LH} />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* フッター */}
        <div
          style={{
            height: 96,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: `0 ${MARGIN}px`,
            fontSize: 24,
            fontWeight: 700,
            color: "rgba(255,255,255,0.5)",
          }}
        >
          <div style={{ display: "flex" }}>ai-tech-express.jp</div>
          <div style={{ display: "flex" }}>{dateOf(article)}</div>
        </div>
      </div>
    ),
    { width: WIDTH, height: HEIGHT, fonts: fontData },
  );
}
