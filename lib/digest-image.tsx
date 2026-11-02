import { promises as fs } from "@/lib/content-fs";
import path from "node:path";
import { loadDefaultJapaneseParser } from "budoux";
import { ImageResponse } from "next/og";
import type { ReactNode } from "react";
import type { DigestDesign } from "@/lib/digest-design";
import type { Digest, DigestStory } from "@/lib/digest";

/**
 * 朝のまとめのシェア画像（app/digest/[id]/share.png・card.png）。
 *
 * どの案も報道テロップ型の約束を守る: 写真は加工しない（下だけ自然に暗くする）・白い Noto Sans JP Black の見出し・
 * 左に発表元の企業色の縦線・赤いラベル・右上に小さなサイト名。黒い板・白い帯・すりガラス・黄色は使わない。
 *   A 写真3枚のコラージュ  B トップ記事を大きく＋残り2本  C AI のイラスト背景＋番号つきの見出し一覧
 * 横長（1200×630。リンクカード・og:image）と縦長（1080×1350。Threads・X に添える画像）の2形。
 */
export type DigestFormat = "wide" | "tall";
export const SIZES: Record<DigestFormat, { width: number; height: number }> = {
  wide: { width: 1200, height: 630 },
  tall: { width: 1080, height: 1350 },
};

const RED = "#d92d20";
const INK = "#0b1a33";
const SHADOW = "0 2px 12px rgba(0,0,0,0.45)";
const SHADE = "linear-gradient(to bottom, rgba(0,0,0,0) 36%, rgba(0,0,0,0.5) 64%, rgba(0,0,0,0.88) 100%)";

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

/** 文節で折り返したときのおおよその行数 */
function lineCount(text: string, size: number, width: number): number {
  return Math.max(1, Math.ceil((emWidth(text) * 1.12) / (width / size)));
}

/** texts がどれも maxLines 行に収まる、いちばん大きい文字サイズ（max〜min） */
function fitSize(texts: string[], width: number, maxLines: number, max: number, min: number): number {
  for (let size = max; size > min; size -= 2) {
    // 行数に加えて、いちばん長い文節が1行に収まること（文節の中で折り返さない）
    const fits = (t: string) => lineCount(t, size, width) <= maxLines && Math.max(...phrasesOf(t).map(emWidth)) * size <= width;
    if (texts.every(fits)) return size;
  }
  return min;
}

/** 文節ごとに分け、単語の途中で改行されないようにする */
function Phrases({ text, size, color = "#fff", weight = 900, lineHeight = 1.28, shadow = true }: { text: string; size: number; color?: string; weight?: 700 | 900; lineHeight?: number; shadow?: boolean }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", fontSize: size, fontWeight: weight, lineHeight, color, ...(shadow ? { textShadow: SHADOW } : {}) }}>
      {phrasesOf(text).map((phrase, index) => (
        <span key={index} style={{ display: "flex", flexShrink: 0 }}>
          {phrase}
        </span>
      ))}
    </div>
  );
}

/**
 * 文節に分ける。BudouX は「新AIモデル『Mistral Large 4』を」のような長い文節を作ることがあり、
 * 1行に入らないと文節の中で折り返して次の文節が1行を占めてしまうので、長い文節は括弧の前後でさらに分ける（製品名の中では切らない）
 */
function phrasesOf(text: string): string[] {
  return parser.parse(text).flatMap((phrase) =>
    emWidth(phrase) <= 8 ? [phrase] : phrase.split(/(?=[『「（])|(?<=[』」）])(?![をがはにでとのへもや、。])/).filter(Boolean),
  );
}

type Photo = string | null;

/** 写真を data URL にする（描画エンジンが読める JPEG・PNG だけ）。自前の /img/ は KV から直接読む */
async function photoData(url: string, readOwn?: (name: string) => Promise<ArrayBuffer | null>): Promise<Photo> {
  if (!url) return null;
  const own = /^(?:https:\/\/[^/]+)?\/img\/([A-Za-z0-9][A-Za-z0-9_-]*\.jpg)$/.exec(url);
  if (own && readOwn) {
    const body = await readOwn(own[1]).catch(() => null);
    if (body) return `data:image/jpeg;base64,${Buffer.from(body).toString("base64")}`;
  }
  const absolute = url.startsWith("/") ? `https://ai-tech-express.jp${url}` : url;
  if (!/^https:\/\//.test(absolute)) return null;
  const candidates = absolute.includes("maxresdefault") ? [absolute, absolute.replace("maxresdefault", "hqdefault")] : [absolute];
  for (const candidate of candidates) {
    try {
      const res = await fetch(candidate, {
        signal: AbortSignal.timeout(8000),
        headers: { "User-Agent": "Mozilla/5.0 (compatible; SaisokuCoverBot/1.0)", Accept: "image/jpeg,image/png" },
      });
      const type = res.headers.get("content-type") ?? "";
      if (!res.ok || !/image\/(jpeg|png)/.test(type)) continue;
      const body = await res.arrayBuffer();
      if (body.byteLength === 0 || body.byteLength > 8 * 1024 * 1024) continue;
      return `data:${type.split(";")[0]};base64,${Buffer.from(body).toString("base64")}`;
    } catch {
      // 次の候補へ
    }
  }
  return null;
}

const weekday = (date: string) => {
  const [y, m, d] = date.split("-").map(Number);
  return { m, d, w: "日月火水木金土"[new Date(Date.UTC(y, m - 1, d)).getUTCDay()] };
};

/** 左上の赤いラベル「今朝のAIニュース 3本」＋日付 */
function Label({ digest, size }: { digest: Digest; size: number }) {
  const { m, d, w } = weekday(digest.date);
  const heading = digest.heading.replace(/(\d+)本$/, " $1本");
  return (
    <div style={{ display: "flex", alignItems: "center", gap: Math.round(size * 0.24) }}>
      <div style={{ display: "flex", padding: `${Math.round(size * 0.16)}px ${Math.round(size * 0.48)}px`, borderRadius: 6, background: RED, color: "#fff", fontSize: size, fontWeight: 900 }}>
        {heading}
      </div>
      <div style={{ display: "flex", padding: `${Math.round(size * 0.16)}px ${Math.round(size * 0.4)}px`, borderRadius: 6, background: INK, color: "#fff", fontSize: Math.round(size * 0.86), fontWeight: 900 }}>
        {`${m}/${d}（${w}）`}
      </div>
    </div>
  );
}

function Logo({ size }: { size: number }) {
  return <div style={{ display: "flex", fontFamily: "Dela", fontSize: size, color: "#fff", textShadow: "0 0 8px rgba(0,0,0,0.55), 0 2px 10px rgba(0,0,0,0.5)" }}>サイソクニュース</div>;
}

/** 写真（加工なし）＋下だけの陰影。写真が無ければ企業色の地 */
function PhotoBox({ photo, color, width, height, children, shade = true }: { photo: Photo; color: string; width: number; height: number; children?: ReactNode; shade?: boolean }) {
  return (
    <div style={{ position: "relative", display: "flex", width, height, background: photo ? "#111" : color, overflow: "hidden" }}>
      {photo && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={photo} alt="" width={width} height={height} style={{ position: "absolute", left: 0, top: 0, width, height, objectFit: "cover" }} />
      )}
      {shade && <div style={{ position: "absolute", left: 0, top: 0, width, height, backgroundImage: SHADE }} />}
      {children}
    </div>
  );
}

/** 見出しのかたまり（企業色の縦線＋番号・企業名＋白い極太の見出し） */
function Telop({ story, rank, size, width, meta }: { story: DigestStory; rank: number; size: number; width: number; meta: number }) {
  const bar = Math.max(5, Math.round(size / 7));
  const gap = Math.round(size * 0.42);
  const color = story.company.slug === "other" ? RED : story.company.color;
  return (
    <div style={{ display: "flex", gap, width }}>
      <div style={{ display: "flex", width: bar, borderRadius: bar / 2, background: color }} />
      <div style={{ display: "flex", flexDirection: "column", gap: Math.round(meta * 0.35), width: width - bar - gap }}>
        <div style={{ display: "flex", alignItems: "center", gap: Math.round(meta * 0.5), color: "#fff", fontSize: meta, fontWeight: 700, textShadow: SHADOW }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: meta * 1.5, height: meta * 1.5, borderRadius: meta, background: "#fff", color: INK, fontWeight: 900, textShadow: "none" }}>{rank}</div>
          {story.company.slug !== "other" && <div style={{ display: "flex" }}>{story.company.name}</div>}
        </div>
        <Phrases text={story.headline} size={size} />
      </div>
    </div>
  );
}

function Credit({ text, size }: { text: string; size: number }) {
  if (!text) return null;
  return <div style={{ display: "flex", fontSize: size, fontWeight: 700, color: "rgba(255,255,255,0.86)", textShadow: "0 1px 6px rgba(0,0,0,0.7)" }}>{text}</div>;
}

type Ctx = { digest: Digest; photos: Photo[]; background: Photo };

/** A: 写真のコラージュ（横長は3列、縦長は3段） */
function DesignA({ digest, photos }: Ctx, format: DigestFormat) {
  const { width, height } = SIZES[format];
  const stories = digest.stories.slice(0, 3);
  const n = stories.length;
  const GAP = 4;
  if (format === "wide") {
    const pw = Math.floor((width - GAP * (n - 1)) / n);
    const textW = pw - 26 - 22;
    const size = fitSize(stories.map((s) => s.headline), textW - 20, 4, 44, 30);
    return (
      <div style={{ display: "flex", width, height, background: INK, position: "relative", gap: GAP }}>
        {stories.map((story, i) => (
          <PhotoBox key={story.slug} photo={photos[i]} color={story.company.color} width={pw} height={height}>
            <div style={{ position: "absolute", left: 26, right: 22, bottom: 30, display: "flex" }}>
              <Telop story={story} rank={i + 1} size={size} width={textW} meta={21} />
            </div>
            <div style={{ position: "absolute", left: 26, top: i === 0 ? 102 : 94, display: "flex" }}>
              <Credit text={story.credit} size={15} />
            </div>
          </PhotoBox>
        ))}
        <div style={{ position: "absolute", left: 26, top: 28, display: "flex" }}>
          <Label digest={digest} size={34} />
        </div>
        <div style={{ position: "absolute", right: 26, top: 34, display: "flex" }}>
          <Logo size={24} />
        </div>
      </div>
    );
  }
  const HEAD = 150;
  const ph = Math.floor((height - HEAD - GAP * (n - 1)) / n);
  const textW = width - 56 - 48;
  const size = fitSize(stories.map((s) => s.headline), textW - 30, 2, 54, 40);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: INK, gap: GAP }}>
      <div style={{ display: "flex", height: HEAD - GAP, alignItems: "center", justifyContent: "space-between", padding: "0 48px" }}>
        <Label digest={digest} size={46} />
        <Logo size={28} />
      </div>
      {stories.map((story, i) => (
        <PhotoBox key={story.slug} photo={photos[i]} color={story.company.color} width={width} height={ph}>
          <div style={{ position: "absolute", left: 56, right: 48, bottom: 30, display: "flex" }}>
            <Telop story={story} rank={i + 1} size={size} width={textW} meta={26} />
          </div>
          <div style={{ position: "absolute", left: 56, top: 22, display: "flex" }}>
            <Credit text={story.credit} size={18} />
          </div>
        </PhotoBox>
      ))}
    </div>
  );
}

/** B: トップ記事を大きく＋残り2本 */
function DesignB({ digest, photos }: Ctx, format: DigestFormat) {
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  const GAP = 4;
  if (format === "wide") {
    const bigW = 760;
    const sideW = width - bigW - GAP;
    const sideH = Math.floor((height - GAP * (rest.length - 1)) / Math.max(rest.length, 1));
    const bigText = bigW - 52 - 40;
    const bigSize = fitSize([top.headline], bigText - 24, 3, 56, 40);
    const sideText = sideW - 24 - 20;
    const sideSize = fitSize(rest.map((s) => s.headline), sideText - 18, 3, 32, 24);
    return (
      <div style={{ display: "flex", width, height, background: INK, position: "relative", gap: GAP }}>
        <PhotoBox photo={photos[0]} color={top.company.color} width={bigW} height={height}>
          <div style={{ position: "absolute", left: 52, right: 40, bottom: 40, display: "flex" }}>
            <Telop story={top} rank={1} size={bigSize} width={bigText} meta={24} />
          </div>
          <div style={{ position: "absolute", left: 40, top: 96, display: "flex" }}>
            <Credit text={top.credit} size={15} />
          </div>
        </PhotoBox>
        <div style={{ display: "flex", flexDirection: "column", gap: GAP }}>
          {rest.map((story, i) => (
            <PhotoBox key={story.slug} photo={photos[i + 1]} color={story.company.color} width={sideW} height={sideH}>
              <div style={{ position: "absolute", left: 24, right: 20, bottom: 20, display: "flex" }}>
                <Telop story={story} rank={i + 2} size={sideSize} width={sideText} meta={17} />
              </div>
              <div style={{ position: "absolute", left: 24, top: i === 0 ? 74 : 16, display: "flex" }}>
                <Credit text={story.credit} size={13} />
              </div>
            </PhotoBox>
          ))}
        </div>
        <div style={{ position: "absolute", left: 40, top: 30, display: "flex" }}>
          <Label digest={digest} size={34} />
        </div>
        <div style={{ position: "absolute", right: 24, top: 22, display: "flex" }}>
          <Logo size={22} />
        </div>
      </div>
    );
  }
  const bigH = 760;
  const rowH = Math.floor((height - bigH - GAP * rest.length) / Math.max(rest.length, 1));
  const bigText = width - 56 - 48;
  const bigSize = fitSize([top.headline], bigText - 30, 3, 64, 46);
  const thumbW = Math.round(rowH * 1.5);
  const rowText = width - thumbW - 40 - 40;
  const rowSize = fitSize(rest.map((s) => s.headline), rowText - 30, 3, 42, 30);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: INK, gap: GAP }}>
      <PhotoBox photo={photos[0]} color={top.company.color} width={width} height={bigH}>
        <div style={{ position: "absolute", left: 48, top: 44, display: "flex" }}>
          <Label digest={digest} size={44} />
        </div>
        <div style={{ position: "absolute", right: 44, top: 52, display: "flex" }}>
          <Logo size={26} />
        </div>
        <div style={{ position: "absolute", left: 56, right: 48, bottom: 44, display: "flex" }}>
          <Telop story={top} rank={1} size={bigSize} width={bigText} meta={28} />
        </div>
      </PhotoBox>
      {rest.map((story, i) => (
        <div key={story.slug} style={{ display: "flex", height: rowH, alignItems: "center", gap: 40 }}>
          <PhotoBox photo={photos[i + 1]} color={story.company.color} width={thumbW} height={rowH} shade={false} />
          <div style={{ display: "flex", width: rowText }}>
            <Telop story={story} rank={i + 2} size={rowSize} width={rowText} meta={24} />
          </div>
        </div>
      ))}
    </div>
  );
}

const TALL_ART = 760;

/** C: AI のイラスト背景＋番号つきの見出し一覧（背景が無い号はトップ記事の写真） */
function DesignC({ digest, photos, background }: Ctx, format: DigestFormat) {
  const { width, height } = SIZES[format];
  const stories = digest.stories.slice(0, 3);
  const wide = format === "wide";
  const listW = wide ? 760 : width - 112;
  const numW = wide ? 64 : 84;
  const size = fitSize(stories.map((s) => s.headline), listW - numW - 28, 2, wide ? 40 : 54, wide ? 28 : 38);
  const bg = background ?? photos[0];
  // 左（横長）・下（縦長）から背景色へ溶かす。イラストの主役は反対側に描かせている（scripts/digest_page.py の AI_SCENE）
  const fade = wide
    ? "linear-gradient(to right, rgba(11,26,51,0.9) 0%, rgba(11,26,51,0.72) 48%, rgba(11,26,51,0) 78%)"
    : `linear-gradient(to bottom, rgba(11,26,51,0) 0px, rgba(11,26,51,0) ${TALL_ART * 0.55}px, rgba(11,26,51,1) ${TALL_ART}px, rgba(11,26,51,1) 100%)`;
  return (
    <div style={{ position: "relative", display: "flex", width, height, background: INK }}>
      {bg && (
        // 縦長は上の方に横長の絵を置く（主役は右側に描かせているので右寄せで切る）
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={bg}
          alt=""
          width={width}
          height={wide ? height : TALL_ART}
          style={{ position: "absolute", left: 0, top: 0, width, height: wide ? height : TALL_ART, objectFit: "cover", objectPosition: wide ? "center" : "right center" }}
        />
      )}
      <div style={{ position: "absolute", left: 0, top: 0, width, height, backgroundImage: fade }} />
      <div style={{ position: "absolute", right: wide ? 32 : 48, top: wide ? 34 : 52, display: "flex" }}>
        <Logo size={wide ? 24 : 28} />
      </div>
      {background && (
        <div style={{ position: "absolute", right: wide ? 32 : 48, bottom: wide ? 22 : 28, display: "flex" }}>
          <Credit text="画像はイメージ（AI生成）" size={wide ? 14 : 18} />
        </div>
      )}
      <div
        style={{
          position: "absolute",
          left: 56,
          ...(wide ? { top: 40 } : { bottom: 90 }),
          width: listW,
          display: "flex",
          flexDirection: "column",
          gap: wide ? 30 : 44,
        }}
      >
        <Label digest={digest} size={wide ? 36 : 48} />
        <div style={{ display: "flex", flexDirection: "column", gap: wide ? 22 : 34 }}>
          {stories.map((story, i) => (
            <div key={story.slug} style={{ display: "flex", alignItems: "flex-start", gap: 28 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", width: numW, height: numW, borderRadius: 10, background: story.company.slug === "other" ? RED : story.company.color, color: "#fff", fontSize: Math.round(numW * 0.6), fontWeight: 900, flexShrink: 0 }}>
                {i + 1}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4, width: listW - numW - 28 }}>
                {story.company.slug !== "other" && (
                  <div style={{ display: "flex", color: "rgba(255,255,255,0.86)", fontSize: wide ? 19 : 26, fontWeight: 700, textShadow: SHADOW }}>{story.company.name}</div>
                )}
                <Phrases text={story.headline} size={size} lineHeight={1.25} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/*
 * B1・B2（2026/11/02 オーナーの指摘を反映した B の改良案）
 * - 写真は 16:9 の枠に丸ごと入れる（切り抜いて文字や人物を欠けさせない）。公式発表の写真は縦横比が違うので余白を付けて全体を出す
 * - 文字は写真に重ねない（無地の地に置く）。色の箱・縦線・ピルは使わない。ラベルは文字だけ
 * - トップ記事をいちばん大きく。番号は赤い数字（文字）だけ
 */
type Theme = { bg: string; ink: string; sub: string; red: string; photoBg: string };
const NAVY_THEME: Theme = { bg: INK, ink: "#ffffff", sub: "rgba(255,255,255,0.72)", red: "#ff5a4f", photoBg: "#06101f" };
const LIGHT_THEME: Theme = { bg: "#f3f5f8", ink: INK, sub: "#4a5870", red: "#c8221a", photoBg: "#e3e8ef" };

/** 16:9 の枠に写真を丸ごと。YouTube・フリー素材・AI 生成はほぼ 16:9 なので枠いっぱい、公式発表の写真は全体が入るように縮める */
function WholePhoto({ photo, story, width, theme }: { photo: Photo; story: DigestStory; width: number; theme: Theme }) {
  const height = Math.round((width * 9) / 16);
  const fit = story.article.imageCredit?.kind === "official" ? "contain" : "cover";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ position: "relative", display: "flex", width, height, borderRadius: 10, overflow: "hidden", background: photo ? theme.photoBg : story.company.color }}>
        {photo && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photo} alt="" width={width} height={height} style={{ position: "absolute", left: 0, top: 0, width, height, objectFit: fit }} />
        )}
      </div>
    </div>
  );
}

/** 「11月2日（月）朝のAIニュース」＋サイト名（文字だけ） */
function Masthead({ digest, size, theme }: { digest: Digest; size: number; theme: Theme }) {
  const { m, d, w } = weekday(digest.date);
  return (
    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", width: "100%" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: Math.round(size * 0.4), color: theme.ink }}>
        <div style={{ display: "flex", fontSize: size, fontWeight: 900 }}>{`${m}月${d}日（${w}）`}</div>
        <div style={{ display: "flex", fontSize: size, fontWeight: 700, color: theme.sub }}>{`朝のAIニュース ${digest.stories.length}本`}</div>
      </div>
      <div style={{ display: "flex", fontFamily: "Dela", fontSize: Math.round(size * 0.92), color: theme.ink }}>サイソクニュース</div>
    </div>
  );
}

/** 番号（赤い数字）＋企業名＋見出し。写真の上には置かない */
function Headline({ story, rank, size, width, theme, meta }: { story: DigestStory; rank: number; size: number; width: number; theme: Theme; meta: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: Math.round(meta * 0.3), width }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: Math.round(meta * 0.5) }}>
        <div style={{ display: "flex", fontSize: Math.round(meta * 1.35), fontWeight: 900, color: theme.red, lineHeight: 1 }}>{rank}</div>
        {story.company.slug !== "other" && <div style={{ display: "flex", fontSize: meta, fontWeight: 700, color: theme.sub, lineHeight: 1 }}>{story.company.name}</div>}
      </div>
      <Phrases text={story.headline} size={size} color={theme.ink} lineHeight={1.3} shadow={false} />
      {story.credit && <div style={{ display: "flex", fontSize: Math.max(13, Math.round(meta * 0.62)), fontWeight: 700, color: theme.sub }}>{story.credit}</div>}
    </div>
  );
}

/** B1: 左にトップ記事（写真の下に見出し）、右に2本を［写真｜見出し］で縦に並べる。紺の地 */
function DesignB1({ digest, photos }: Ctx, format: DigestFormat) {
  const theme = NAVY_THEME;
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  if (format === "wide") {
    const P = 36;
    const leftW = 560;
    const rightX = P + leftW + 40;
    const rightW = width - P - rightX;
    const thumbW = 232;
    const rowText = rightW - thumbW - 20;
    const topSize = fitSize([top.headline], leftW - 10, 2, 46, 32);
    const rowSize = fitSize(rest.map((s) => s.headline), rowText - 8, 4, 28, 22);
    return (
      <div style={{ display: "flex", flexDirection: "column", width, height, background: theme.bg, padding: `${P - 6}px ${P}px ${P}px` }}>
        <Masthead digest={digest} size={26} theme={theme} />
        <div style={{ display: "flex", gap: 40, marginTop: 22 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16, width: leftW }}>
            <WholePhoto photo={photos[0]} story={top} width={leftW} theme={theme} />
            <Headline story={top} rank={1} size={topSize} width={leftW} theme={theme} meta={22} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-around", gap: 30, width: rightW, height: height - 2 * P - 58 }}>
            {rest.map((story, i) => (
              <div key={story.slug} style={{ display: "flex", gap: 20 }}>
                <WholePhoto photo={photos[i + 1]} story={story} width={thumbW} theme={theme} />
                <Headline story={story} rank={i + 2} size={rowSize} width={rowText} theme={theme} meta={18} />
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }
  const P = 48;
  const inner = width - P * 2;
  const thumbW = 360;
  const rowText = inner - thumbW - 28;
  const topSize = fitSize([top.headline], inner - 10, 2, 58, 44);
  const rowSize = fitSize(rest.map((s) => s.headline), rowText - 10, 4, 38, 30);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: theme.bg, padding: `${P - 4}px ${P}px ${P}px` }}>
      <Masthead digest={digest} size={34} theme={theme} />
      <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 28 }}>
        <WholePhoto photo={photos[0]} story={top} width={inner} theme={theme} />
        <Headline story={top} rank={1} size={topSize} width={inner} theme={theme} meta={28} />
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 30, marginTop: 40 }}>
        {rest.map((story, i) => (
          <div key={story.slug} style={{ display: "flex", gap: 28 }}>
            <WholePhoto photo={photos[i + 1]} story={story} width={thumbW} theme={theme} />
            <Headline story={story} rank={i + 2} size={rowSize} width={rowText} theme={theme} meta={24} />
          </div>
        ))}
      </div>
    </div>
  );
}

/** B2: 上にトップ記事［写真｜大きな見出し］、下に2本を［写真｜見出し］で横に並べる。サイトと同じ明るい地 */
function DesignB2({ digest, photos }: Ctx, format: DigestFormat) {
  const theme = LIGHT_THEME;
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  if (format === "wide") {
    const P = 36;
    const inner = width - P * 2;
    const topW = 540;
    const topText = inner - topW - 36;
    const cellW = Math.floor((inner - 36) / 2);
    const thumbW = 248;
    const cellText = cellW - thumbW - 18;
    const topSize = fitSize([top.headline], topText - 10, 3, 50, 36);
    const cellSize = fitSize(rest.map((s) => s.headline), cellText - 8, 4, 26, 20);
    return (
      <div style={{ display: "flex", flexDirection: "column", width, height, background: theme.bg, padding: `${P - 6}px ${P}px ${P}px` }}>
        <Masthead digest={digest} size={26} theme={theme} />
        <div style={{ display: "flex", gap: 36, marginTop: 22, alignItems: "center" }}>
          <WholePhoto photo={photos[0]} story={top} width={topW} theme={theme} />
          <Headline story={top} rank={1} size={topSize} width={topText} theme={theme} meta={22} />
        </div>
        <div style={{ display: "flex", gap: 36, marginTop: 28 }}>
          {rest.map((story, i) => (
            <div key={story.slug} style={{ display: "flex", gap: 18, width: cellW }}>
              <WholePhoto photo={photos[i + 1]} story={story} width={thumbW} theme={theme} />
              <Headline story={story} rank={i + 2} size={cellSize} width={cellText} theme={theme} meta={17} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  const P = 48;
  const inner = width - P * 2;
  const cellW = Math.floor((inner - 32) / 2);
  const topSize = fitSize([top.headline], inner - 10, 2, 58, 44);
  const cellSize = fitSize(rest.map((s) => s.headline), cellW - 70, 3, 32, 24);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: theme.bg, padding: `${P - 4}px ${P}px ${P}px` }}>
      <Masthead digest={digest} size={34} theme={theme} />
      <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 28 }}>
        <WholePhoto photo={photos[0]} story={top} width={inner} theme={theme} />
        <Headline story={top} rank={1} size={topSize} width={inner} theme={theme} meta={28} />
      </div>
      <div style={{ display: "flex", gap: 32, marginTop: 28 }}>
        {rest.map((story, i) => (
          <div key={story.slug} style={{ display: "flex", flexDirection: "column", gap: 12, width: cellW }}>
            <WholePhoto photo={photos[i + 1]} story={story} width={cellW} theme={theme} />
            <Headline story={story} rank={i + 2} size={cellSize} width={cellW} theme={theme} meta={22} />
          </div>
        ))}
      </div>
    </div>
  );
}

const DESIGNS = { A: DesignA, B: DesignB, B1: DesignB1, B2: DesignB2, C: DesignC } as const;

export async function renderDigestImage(
  digest: Digest,
  design: DigestDesign,
  format: DigestFormat,
  readOwn?: (name: string) => Promise<ArrayBuffer | null>,
): Promise<ImageResponse> {
  const needPhotos = design === "C" ? (digest.aiImage ? [] : digest.stories.slice(0, 1)) : digest.stories.slice(0, 3);
  const [photos, background, fontData] = await Promise.all([
    Promise.all(needPhotos.map((s) => photoData(s.photo, readOwn))),
    design === "C" && digest.aiImage ? photoData(digest.aiImage, readOwn) : Promise.resolve(null),
    loadFonts(),
  ]);
  const { width, height } = SIZES[format];
  return new ImageResponse(DESIGNS[design]({ digest, photos, background }, format), {
    width,
    height,
    fonts: fontData,
  });
}
