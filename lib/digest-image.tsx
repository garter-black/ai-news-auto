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
function WholePhoto({ photo, story, width, theme, radius = 10 }: { photo: Photo; story: DigestStory; width: number; theme: Theme; radius?: number }) {
  const height = Math.round((width * 9) / 16);
  const fit = story.article.imageCredit?.kind === "official" ? "contain" : "cover";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ position: "relative", display: "flex", width, height, borderRadius: radius, overflow: "hidden", background: photo ? theme.photoBg : story.company.color }}>
        {photo && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photo} alt="" width={width} height={height} style={{ position: "absolute", left: 0, top: 0, width, height, objectFit: fit }} />
        )}
      </div>
    </div>
  );
}

/** 「11月2日（月）朝のAIニュース」＋サイト名（文字だけ） */
function Masthead({ digest, size, theme, stack = false }: { digest: Digest; size: number; theme: Theme; stack?: boolean }) {
  const { m, d, w } = weekday(digest.date);
  return (
    <div style={{ display: "flex", flexDirection: stack ? "column-reverse" : "row", alignItems: stack ? "flex-start" : "baseline", justifyContent: "space-between", gap: stack ? 10 : 0, width: "100%" }}>
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

/* ---------- D1〜D4（2026/11/02 オーナー向けの別方向の案。写真は切らない・文字は写真に重ねない・箱や線なし・書体2つ・差し色1つ） ---------- */
const PAPER: Theme = { bg: "#ffffff", ink: "#0b1a33", sub: "#5b6880", red: "#c8221a", photoBg: "#eef1f5" };
const APP_BG = "#eef1f5";

/** 番号＋見出しだけの1行（写真なし） */
function ListItem({ story, rank, size, width, theme }: { story: DigestStory; rank: number; size: number; width: number; theme: Theme }) {
  return (
    <div style={{ display: "flex", gap: Math.round(size * 0.55), width }}>
      <div style={{ display: "flex", fontSize: Math.round(size * 1.15), fontWeight: 900, color: theme.red, lineHeight: 1.12, width: Math.round(size * 0.9), flexShrink: 0 }}>{rank}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, width: width - Math.round(size * 1.45) }}>
        {story.company.slug !== "other" && <div style={{ display: "flex", fontSize: Math.round(size * 0.62), fontWeight: 700, color: theme.sub }}>{story.company.name}</div>}
        <Phrases text={story.headline} size={size} color={theme.ink} lineHeight={1.3} shadow={false} />
      </div>
    </div>
  );
}

/** D1 雑誌の表紙型: トップの写真を端から端まで大きく、その下に大きな見出し。2・3位は番号つきの文字だけ */
function DesignD1({ digest, photos }: Ctx, format: DigestFormat) {
  const t = PAPER;
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  if (format === "wide") {
    const photoW = 720;
    const P = 40;
    const rightW = width - photoW - P * 2;
    const topSize = fitSize([top.headline], rightW - 10, 3, 46, 32);
    const listSize = fitSize(rest.map((s) => s.headline), rightW - 60, 3, 26, 20);
    return (
      <div style={{ display: "flex", width, height, background: t.bg }}>
        <div style={{ display: "flex", flexDirection: "column", width: photoW, height, background: t.photoBg, justifyContent: "center" }}>
          <WholePhoto photo={photos[0]} story={top} width={photoW} theme={t} radius={0} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", width: rightW + P * 2, padding: `${P - 6}px ${P}px ${P}px`, gap: 26 }}>
          <Masthead digest={digest} size={20} theme={t} stack />
          <Headline story={top} rank={1} size={topSize} width={rightW} theme={t} meta={20} />
          <div style={{ display: "flex", flexDirection: "column", gap: 20, marginTop: 6 }}>
            {rest.map((story, i) => <ListItem key={story.slug} story={story} rank={i + 2} size={listSize} width={rightW} theme={t} />)}
          </div>
        </div>
      </div>
    );
  }
  const P = 52;
  const inner = width - P * 2;
  const topSize = fitSize([top.headline], inner - 10, 2, 66, 48);
  const listSize = fitSize(rest.map((s) => s.headline), inner - 90, 2, 40, 30);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: t.bg }}>
      <div style={{ display: "flex", padding: `40px ${P}px 30px` }}>
        <Masthead digest={digest} size={32} theme={t} />
      </div>
      <WholePhoto photo={photos[0]} story={top} width={width} theme={t} radius={0} />
      <div style={{ display: "flex", flexDirection: "column", padding: `30px ${P}px 0`, gap: 40 }}>
        <Headline story={top} rank={1} size={topSize} width={inner} theme={t} meta={26} />
        <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
          {rest.map((story, i) => <ListItem key={story.slug} story={story} rank={i + 2} size={listSize} width={inner} theme={t} />)}
        </div>
      </div>
    </div>
  );
}

/** カード（白・角丸・やわらかい影だけ） */
function Card({ children, width, pad }: { children: ReactNode; width: number; pad: number }) {
  return (
    <div style={{ display: "flex", width, padding: pad, borderRadius: 18, background: "#fff", boxShadow: "0 6px 22px rgba(11,26,51,0.08)" }}>{children}</div>
  );
}

/** D2 ニュースアプリ型: カードを縦に3枚。トップは写真を上に大きく、2・3位は［写真｜見出し］ */
function DesignD2({ digest, photos }: Ctx, format: DigestFormat) {
  const t = { ...PAPER, bg: APP_BG, photoBg: "#e3e8ef" };
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  if (format === "wide") {
    const P = 32;
    const leftW = 560;
    const rightW = width - P * 2 - leftW - 24;
    const pad = 18;
    const topSize = fitSize([top.headline], leftW - pad * 2 - 10, 2, 36, 26);
    const thumbW = 190;
    const rowText = rightW - pad * 2 - thumbW - 18;
    const rowSize = fitSize(rest.map((s) => s.headline), rowText - 8, 4, 26, 20);
    return (
      <div style={{ display: "flex", flexDirection: "column", width, height, background: t.bg, padding: `26px ${P}px ${P}px` }}>
        <Masthead digest={digest} size={22} theme={t} />
        <div style={{ display: "flex", gap: 24, marginTop: 18 }}>
          <Card width={leftW} pad={pad}>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <WholePhoto photo={photos[0]} story={top} width={leftW - pad * 2} theme={t} />
              <Headline story={top} rank={1} size={topSize} width={leftW - pad * 2} theme={t} meta={18} />
            </div>
          </Card>
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {rest.map((story, i) => (
              <Card key={story.slug} width={rightW} pad={pad}>
                <div style={{ display: "flex", gap: 18 }}>
                  <WholePhoto photo={photos[i + 1]} story={story} width={thumbW} theme={t} />
                  <Headline story={story} rank={i + 2} size={rowSize} width={rowText} theme={t} meta={16} />
                </div>
              </Card>
            ))}
          </div>
        </div>
      </div>
    );
  }
  const P = 40;
  const inner = width - P * 2;
  const pad = 26;
  const cw = inner - pad * 2;
  const topSize = fitSize([top.headline], cw - 10, 2, 54, 40);
  const thumbW = 300;
  const rowText = cw - thumbW - 26;
  const rowSize = fitSize(rest.map((s) => s.headline), rowText - 10, 4, 36, 28);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: t.bg, padding: `40px ${P}px ${P}px` }}>
      <Masthead digest={digest} size={32} theme={t} />
      <div style={{ display: "flex", flexDirection: "column", gap: 20, marginTop: 24 }}>
        <Card width={inner} pad={pad}>
          <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
            <WholePhoto photo={photos[0]} story={top} width={cw} theme={t} />
            <Headline story={top} rank={1} size={topSize} width={cw} theme={t} meta={26} />
          </div>
        </Card>
        {rest.map((story, i) => (
          <Card key={story.slug} width={inner} pad={pad}>
            <div style={{ display: "flex", gap: 26, alignItems: "center" }}>
              <WholePhoto photo={photos[i + 1]} story={story} width={thumbW} theme={t} />
              <Headline story={story} rank={i + 2} size={rowSize} width={rowText} theme={t} meta={22} />
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

/** 大きな日付「11.2 MON」 */
function BigDate({ digest, size, theme }: { digest: Digest; size: number; theme: Theme }) {
  const { m, d } = weekday(digest.date);
  const [y, mm, dd] = digest.date.split("-").map(Number);
  const en = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][new Date(Date.UTC(y, mm - 1, dd)).getUTCDay()];
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: Math.round(size * 0.18), color: theme.ink, lineHeight: 1 }}>
      <div style={{ display: "flex", fontSize: size, fontWeight: 900, letterSpacing: -Math.round(size * 0.03) }}>{`${m}.${d}`}</div>
      <div style={{ display: "flex", fontSize: Math.round(size * 0.34), fontWeight: 900, color: theme.red }}>{en}</div>
    </div>
  );
}

/** D3 タイポグラフィ型: 大きな日付を主役に、新聞の1面のような見出しの大小 */
function DesignD3({ digest, photos }: Ctx, format: DigestFormat) {
  const t = { ...PAPER, bg: "#fbfaf7" };
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  const label = (size: number) => (
    <div style={{ display: "flex", flexDirection: "column", gap: Math.round(size * 0.35) }}>
      <div style={{ display: "flex", fontSize: size, fontWeight: 700, color: t.sub }}>{`朝のAIニュース ${digest.stories.length}本`}</div>
      <div style={{ display: "flex", fontFamily: "Dela", fontSize: Math.round(size * 0.92), color: t.ink }}>サイソクニュース</div>
    </div>
  );
  if (format === "wide") {
    const P = 40;
    const leftW = 400;
    const photoW = width - P * 2 - leftW - 40;
    const topSize = fitSize([top.headline], photoW - 10, 2, 40, 28);
    const listSize = fitSize(rest.map((s) => s.headline), leftW - 50, 3, 26, 20);
    return (
      <div style={{ display: "flex", width, height, background: t.bg, padding: `${P}px` , gap: 40 }}>
        <div style={{ display: "flex", flexDirection: "column", width: leftW, gap: 26 }}>
          <BigDate digest={digest} size={118} theme={t} />
          {label(22)}
          <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 8 }}>
            {rest.map((story, i) => <ListItem key={story.slug} story={story} rank={i + 2} size={listSize} width={leftW} theme={t} />)}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, width: photoW }}>
          <WholePhoto photo={photos[0]} story={top} width={photoW} theme={t} radius={4} />
          <Headline story={top} rank={1} size={topSize} width={photoW} theme={t} meta={20} />
        </div>
      </div>
    );
  }
  const P = 56;
  const inner = width - P * 2;
  const colW = Math.floor((inner - 44) / 2);
  const topSize = fitSize([top.headline], inner - 10, 2, 66, 48);
  const colSize = fitSize(rest.map((s) => s.headline), colW - 70, 3, 36, 28);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: t.bg, padding: `48px ${P}px ${P}px` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
        <BigDate digest={digest} size={150} theme={t} />
        {label(30)}
      </div>
      <div style={{ display: "flex", marginTop: 34 }}>
        <Headline story={top} rank={1} size={topSize} width={inner} theme={t} meta={26} />
      </div>
      <div style={{ display: "flex", marginTop: 22 }}>
        <WholePhoto photo={photos[0]} story={top} width={inner} theme={t} radius={4} />
      </div>
      <div style={{ display: "flex", gap: 44, marginTop: 36 }}>
        {rest.map((story, i) => <ListItem key={story.slug} story={story} rank={i + 2} size={colSize} width={colW} theme={t} />)}
      </div>
    </div>
  );
}

/** D4 グリッド型: トップは幅いっぱい（写真の上・見出しの下）、2・3位は同じ大きさのカードを横に（写真の上・見出しの下） */
function DesignD4({ digest, photos }: Ctx, format: DigestFormat) {
  const t = PAPER;
  const { width, height } = SIZES[format];
  const [top, ...rest] = digest.stories.slice(0, 3);
  if (format === "wide") {
    const P = 36;
    const gap = 28;
    const topW = 520;
    const colW = Math.floor((width - P * 2 - topW - gap * 2) / 2);
    const topSize = fitSize([top.headline], topW - 10, 3, 38, 28);
    const colSize = fitSize(rest.map((s) => s.headline), colW - 8, 5, 24, 19);
    return (
      <div style={{ display: "flex", flexDirection: "column", width, height, background: t.bg, padding: `28px ${P}px ${P}px` }}>
        <Masthead digest={digest} size={22} theme={t} />
        <div style={{ display: "flex", gap, marginTop: 22 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14, width: topW }}>
            <WholePhoto photo={photos[0]} story={top} width={topW} theme={t} />
            <Headline story={top} rank={1} size={topSize} width={topW} theme={t} meta={18} />
          </div>
          {rest.map((story, i) => (
            <div key={story.slug} style={{ display: "flex", flexDirection: "column", gap: 12, width: colW }}>
              <WholePhoto photo={photos[i + 1]} story={story} width={colW} theme={t} />
              <Headline story={story} rank={i + 2} size={colSize} width={colW} theme={t} meta={16} />
            </div>
          ))}
        </div>
      </div>
    );
  }
  const P = 40;
  const inner = width - P * 2;
  const gap = 32;
  const colW = Math.floor((inner - gap) / 2);
  const topSize = fitSize([top.headline], inner - 10, 2, 56, 42);
  const colSize = fitSize(rest.map((s) => s.headline), colW - 10, 3, 32, 26);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: t.bg, padding: `36px ${P}px ${P}px` }}>
      <Masthead digest={digest} size={30} theme={t} />
      <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 22 }}>
        <WholePhoto photo={photos[0]} story={top} width={inner} theme={t} />
        <Headline story={top} rank={1} size={topSize} width={inner} theme={t} meta={24} />
      </div>
      <div style={{ display: "flex", gap, marginTop: 30 }}>
        {rest.map((story, i) => (
          <div key={story.slug} style={{ display: "flex", flexDirection: "column", gap: 12, width: colW }}>
            <WholePhoto photo={photos[i + 1]} story={story} width={colW} theme={t} />
            <Headline story={story} rank={i + 2} size={colSize} width={colW} theme={t} meta={20} />
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---------- E1・E2（最初の B 案を文字少なくスッキリ。写真主役・紺の地・テロップの雰囲気） ---------- */
const E_BG = "#0b111c";

/** 画像用のさらに短い見出し（全角換算 maxEm 以内）。「AI半導体のNVIDIAが…」→「NVIDIAが…」、「X、新AI「Y」を発表」→「新AI「Y」を発表」 */
function compactHeadline(headline: string, maxEm: number): string {
  const candidates = [headline];
  const comma = headline.search(/[、，]/);
  const tail = comma > 0 ? headline.slice(comma + 1) : "";
  const noPrefix = headline.replace(/^[^、，「『]{1,8}?の(?=[A-Za-z゠-ヿ])/, "");
  if (noPrefix !== headline) candidates.push(noPrefix);
  if (tail) candidates.push(tail);
  const bracket = /[「『]([^」』]+)[」』](を|が)?(.{0,6})$/.exec(headline);
  if (bracket) candidates.push(`${bracket[1]}${bracket[2] ?? ""}${bracket[3]}`.replace(/を(発表|公開|開始)$/, "を$1"));
  for (const text of candidates) if (emWidth(text) <= maxEm) return text;
  const base = candidates[candidates.length - 1];
  let out = "";
  for (const char of base) {
    if (emWidth(out + char) > maxEm - 1) break;
    out += char;
  }
  return `${out}…`;
}

/** 写真（16:9 を幅いっぱい・切らない）の下側だけ地の色へ溶かし、その上に小さな番号と短い見出し */
function ETile({ photo, story, rank, width, height, size, numSize, pad, photoHeight }: { photo: Photo; story: DigestStory; rank: number; width: number; height: number; size: number; numSize: number; pad: number; photoHeight?: number }) {
  const ph = photoHeight ?? Math.round((width * 9) / 16);
  const fade = `linear-gradient(to bottom, rgba(11,17,28,0) ${Math.round(ph * 0.62)}px, rgba(11,17,28,0.85) ${ph - 4}px, ${E_BG} ${ph}px, ${E_BG} 100%)`;
  return (
    <div style={{ position: "relative", display: "flex", width, height, background: photo ? E_BG : story.company.color, overflow: "hidden" }}>
      {photo && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={photo} alt="" width={width} height={ph} style={{ position: "absolute", left: 0, top: 0, width, height: ph, objectFit: "cover", objectPosition: "center top" }} />
      )}
      <div style={{ position: "absolute", left: 0, top: 0, width, height, backgroundImage: fade }} />
      <div style={{ position: "absolute", left: pad, right: pad, bottom: Math.round(pad * 0.8), display: "flex", alignItems: "baseline", gap: Math.round(numSize * 0.6) }}>
        <div style={{ display: "flex", fontSize: numSize, fontWeight: 900, color: "#ff5a4f", textShadow: SHADOW, flexShrink: 0 }}>{rank}</div>
        <Phrases text={story.headline} size={size} lineHeight={1.25} />
      </div>
    </div>
  );
}

function EHeader({ digest, size, height, pad }: { digest: Digest; size: number; height: number; pad: number }) {
  const { m, d } = weekday(digest.date);
  return (
    <div style={{ display: "flex", height, alignItems: "center", justifyContent: "space-between", padding: `0 ${pad}px` }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: Math.round(size * 0.5), color: "#fff" }}>
        <div style={{ display: "flex", fontSize: size, fontWeight: 900 }}>{`${m}/${d}`}</div>
        <div style={{ display: "flex", fontSize: size, fontWeight: 700, color: "rgba(255,255,255,0.78)" }}>朝のAIニュース</div>
      </div>
      <div style={{ display: "flex", fontFamily: "Dela", fontSize: Math.round(size * 0.92), color: "#fff" }}>サイソクニュース</div>
    </div>
  );
}

function designE(variant: 1 | 2) {
  return function DesignE({ digest, photos }: Ctx, format: DigestFormat) {
    const { width, height } = SIZES[format];
    const stories = digest.stories.slice(0, 3).map((s, i) => ({ ...s, headline: compactHeadline(s.headline, i === 0 ? 16 : 12) }));
    const [top, ...rest] = stories;
    const GAP = 4;
    if (format === "wide") {
      const big = variant === 2; // E2（採用案）は文字を大きく（2026/11/02 オーナー指摘）
      const HEAD = big ? 64 : 58;
      const bodyH = height - HEAD;
      const bigW = variant === 1 ? 760 : 840;
      const sideW = width - bigW - GAP;
      const sideH = Math.floor((bodyH - GAP) / 2);
      const topSize = big ? fitSize([top.headline], bigW - 120, 2, 66, 62) : fitSize([top.headline], bigW - 110, 2, 46, 34);
      const sideSize = big ? fitSize(rest.map((s) => s.headline), sideW - 70, 2, 36, 34) : fitSize(rest.map((s) => s.headline), sideW - 80, 2, 26, 20);
      return (
        <div style={{ display: "flex", flexDirection: "column", width, height, background: E_BG }}>
          <EHeader digest={digest} size={big ? 30 : 24} height={HEAD} pad={28} />
          <div style={{ display: "flex", gap: GAP }}>
            <ETile photo={photos[0]} story={top} rank={1} width={bigW} height={bodyH} size={topSize} numSize={big ? 40 : 30} pad={32} />
            <div style={{ display: "flex", flexDirection: "column", gap: GAP }}>
              {rest.map((story, i) => (
                <ETile key={story.slug} photo={photos[i + 1]} story={story} rank={i + 2} width={sideW} height={sideH} size={sideSize} numSize={big ? 26 : 20} pad={18} />
              ))}
            </div>
          </div>
        </div>
      );
    }
    const HEAD = 96;
    if (variant === 1) {
      const topPh = Math.round((width * 9) / 16);
      const topH = topPh + 200;
      const cellW = Math.floor((width - GAP) / 2);
      const cellH = height - HEAD - topH - GAP;
      const topSize = fitSize([top.headline], width - 140, 2, 66, 48);
      const cellSize = fitSize(rest.map((s) => s.headline), cellW - 90, 2, 36, 28);
      return (
        <div style={{ display: "flex", flexDirection: "column", width, height, background: E_BG }}>
          <EHeader digest={digest} size={34} height={HEAD} pad={44} />
          <ETile photo={photos[0]} story={top} rank={1} width={width} height={topH} size={topSize} numSize={40} pad={44} />
          <div style={{ display: "flex", gap: GAP, marginTop: GAP }}>
            {rest.map((story, i) => (
              <ETile key={story.slug} photo={photos[i + 1]} story={story} rank={i + 2} width={cellW} height={cellH} size={cellSize} numSize={28} pad={26} />
            ))}
          </div>
        </div>
      );
    }
    // E2 縦長: トップは写真の下側に見出し、2・3位は［写真｜見出し］の横長の段
    const HEAD2 = 108;
    const topPh = Math.round((width * 9) / 16);
    const topH = topPh + 150;
    const rowH = Math.floor((height - HEAD2 - topH - GAP * 2) / 2);
    const thumbW = Math.round((rowH * 16) / 9);
    const rowText = width - thumbW - 64;
    const topSize = fitSize([top.headline], width - 150, 2, 92, 86);
    const rowSize = fitSize(rest.map((s) => s.headline), rowText - 64, 2, 56, 52);
    return (
      <div style={{ display: "flex", flexDirection: "column", width, height, background: E_BG }}>
        <EHeader digest={digest} size={42} height={HEAD2} pad={44} />
        <ETile photo={photos[0]} story={top} rank={1} width={width} height={topH} size={topSize} numSize={52} pad={44} />
        {rest.map((story, i) => (
          <div key={story.slug} style={{ display: "flex", height: rowH, marginTop: GAP, alignItems: "center", gap: 32 }}>
            <div style={{ display: "flex", width: thumbW, height: rowH, background: "#111", overflow: "hidden" }}>
              {photos[i + 1] && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={photos[i + 1]!} alt="" width={thumbW} height={rowH} style={{ width: thumbW, height: rowH, objectFit: "cover", objectPosition: "center top" }} />
              )}
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 16, width: rowText }}>
              <div style={{ display: "flex", fontSize: 40, fontWeight: 900, color: "#ff5a4f", flexShrink: 0 }}>{i + 2}</div>
              <div style={{ display: "flex", width: rowText - 60 }}>
                <Phrases text={story.headline} size={rowSize} lineHeight={1.25} shadow={false} />
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  };
}

/* ---------- E2（採用案・2026/11/02）。見出しは行を計算して組む（文節で折り返し・はみ出しは描画を失敗させる） ---------- */
/** 全角=1 として、Noto Sans JP Black での文字幅を少し多めに見積もる */
function charEm(c: string): number {
  if (/[A-Z0-9]/.test(c)) return 0.7;
  if (/[a-z]/.test(c)) return 0.6;
  if (c === " ") return 0.28;
  if (/[\u0000-ÿ]/.test(c)) return 0.5;
  return 1;
}
function textEm(text: string): number {
  let total = 0;
  for (const c of text) total += charEm(c);
  return total;
}

/** 折り返しの単位。文節（BudouX）を基本に、製品名・括弧の直後の「を」は次の語とつなぐ（「「Genie 4」／を発表」） */
function wrapTokens(text: string): string[] {
  const raw = phrasesOf(text).flatMap((p) => p.split(/(?<=[A-Za-z0-9」』])(?=を)/));
  const out: string[] = [];
  for (let i = 0; i < raw.length; i++) {
    const t = raw[i];
    if (t === "を" && i + 1 < raw.length) {
      raw[i + 1] = t + raw[i + 1];
      continue;
    }
    if (t === "を" && out.length) {
      out[out.length - 1] += t;
      continue;
    }
    out.push(t);
  }
  return out;
}

/** 幅に収まる行（1行で入れば1行、だめなら行の長さがそろう位置で2行。3行以上は前から詰める）。入らなければ null */
function layoutLines(text: string, size: number, width: number, maxLines: number): string[] | null {
  const max = width / size;
  if (textEm(text) <= max) return [text];
  const tokens = wrapTokens(text);
  if (tokens.some((t) => textEm(t.trim()) > max)) return null;
  if (maxLines < 2) return null;
  let best: string[] | null = null;
  let bestCost = Infinity;
  for (let i = 1; i < tokens.length; i++) {
    const a = tokens.slice(0, i).join("").trim();
    const b = tokens.slice(i).join("").trim();
    if (textEm(a) > max || textEm(b) > max) continue;
    const cost = Math.max(textEm(a), textEm(b)) - Math.min(textEm(a), textEm(b)) * 0.01;
    if (cost < bestCost) {
      bestCost = cost;
      best = [a, b];
    }
  }
  if (best) return best;
  const lines: string[] = [];
  let line = "";
  for (const t of tokens) {
    if (textEm(line + t) > max && line) {
      lines.push(line.trim());
      line = t;
    } else line += t;
  }
  if (line) lines.push(line.trim());
  return lines.length <= maxLines ? lines : null;
}

/** いちばん大きい文字サイズで行を組む。最小でも入らなければ見出しを短くし、それでも無理なら例外（はみ出して切れた画像は出さない） */
function fitLines(text: string, width: number, maxLines: number, max: number, min: number, maxEm: number): { lines: string[]; size: number } {
  for (const candidate of [text, compactHeadline(text, maxEm - 2), compactHeadline(text, maxEm - 4)]) {
    // 行数の少ない組み方を優先（同じ行数ならいちばん大きい文字）
    for (let n = 1; n <= maxLines; n++) {
      for (let size = max; size >= min; size -= 2) {
        const lines = layoutLines(candidate, size, width, n);
        if (lines && lines.length === n) return { lines, size };
      }
    }
  }
  throw new Error(`digest headline does not fit: ${text}`);
}

function Lines({ lines, size, color = "#fff", shadow = false }: { lines: string[]; size: number; color?: string; shadow?: boolean }) {
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {lines.map((line, i) => (
        <div key={i} style={{ display: "flex", whiteSpace: "nowrap", fontSize: size, fontWeight: 900, lineHeight: 1.22, color, ...(shadow ? { textShadow: SHADOW } : {}) }}>
          {line}
        </div>
      ))}
    </div>
  );
}

const E_RED = "#ff5a4f";

/** 16:9 の写真を切らずに、与えた高さに収める（横は中央寄せ。余りは地の色） */
function EPhoto({ photo, story, width, height }: { photo: Photo; story: DigestStory; width: number; height: number }) {
  const pw = Math.min(width, Math.round((height * 16) / 9));
  const ph = Math.round((pw * 9) / 16);
  return (
    <div style={{ display: "flex", width, height, justifyContent: "center", alignItems: "flex-start", background: E_BG }}>
      <div style={{ display: "flex", width: pw, height: ph, background: photo ? "#111" : story.company.color, overflow: "hidden" }}>
        {photo && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photo} alt="" width={pw} height={ph} style={{ width: pw, height: ph, objectFit: "cover", objectPosition: "center top" }} />
        )}
      </div>
    </div>
  );
}

function DesignE2({ digest, photos }: Ctx, format: DigestFormat) {
  const { width, height } = SIZES[format];
  const stories = digest.stories.slice(0, 3).map((s, i) => ({ ...s, headline: compactHeadline(s.headline, i === 0 ? 16 : 12) }));
  const [top, ...rest] = stories;
  const GAP = 4;
  if (format === "wide") {
    const HEAD = 64;
    const bodyH = height - HEAD;
    const bigW = 760;
    const sideW = width - bigW - GAP;
    const sideH = Math.floor((bodyH - GAP) / 2);
    const PAD = 32;
    const num = 40;
    const topText = fitLines(top.headline, bigW - PAD * 2 - num - 14, 2, 66, 60, 16);
    const topBlock = Math.round(topText.lines.length * topText.size * 1.22) + 14 + 24;
    const sideNum = 26;
    const sideFit = rest.map((s) => fitLines(s.headline, sideW - 36 - sideNum - 10, 2, 34, 30, 12));
    const sideSize = Math.min(...sideFit.map((f) => f.size));
    const sideLines = rest.map((s) => fitLines(s.headline, sideW - 36 - sideNum - 10, 2, sideSize, sideSize, 12).lines);
    return (
      <div style={{ display: "flex", flexDirection: "column", width, height, background: E_BG }}>
        <EHeader digest={digest} size={30} height={HEAD} pad={28} />
        <div style={{ display: "flex", gap: GAP }}>
          <div style={{ display: "flex", flexDirection: "column", width: bigW, height: bodyH }}>
            <EPhoto photo={photos[0]} story={top} width={bigW} height={bodyH - topBlock} />
            <div style={{ display: "flex", alignItems: "baseline", gap: 14, padding: `14px ${PAD}px 0` }}>
              <div style={{ display: "flex", fontSize: num, fontWeight: 900, color: E_RED, width: num, flexShrink: 0 }}>1</div>
              <Lines lines={topText.lines} size={topText.size} />
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: GAP }}>
            {rest.map((story, i) => {
              const block = Math.round(sideLines[i].length * sideSize * 1.22) + 10 + 14;
              return (
                <div key={story.slug} style={{ display: "flex", flexDirection: "column", width: sideW, height: sideH, background: E_BG }}>
                  <EPhoto photo={photos[i + 1]} story={story} width={sideW} height={sideH - block} />
                  <div style={{ display: "flex", alignItems: "baseline", gap: 10, padding: "8px 18px 0" }}>
                    <div style={{ display: "flex", fontSize: sideNum, fontWeight: 900, color: E_RED, width: sideNum, flexShrink: 0 }}>{i + 2}</div>
                    <Lines lines={sideLines[i]} size={sideSize} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }
  const HEAD = 108;
  const SAFE = 48;
  const num = 52;
  const topPh = Math.round((width * 9) / 16);
  const topText = fitLines(top.headline, width - SAFE * 2 - num - 16, 2, 92, 84, 16);
  const topBlock = Math.round(topText.lines.length * topText.size * 1.22) + 22 + 28;
  const rowH = Math.floor((height - HEAD - topPh - topBlock - GAP * 2) / 2);
  const thumbW = Math.round((rowH * 16) / 9);
  const rowNum = 40;
  const rowTextW = width - thumbW - 32 - SAFE - rowNum - 14;
  const rowFits = rest.map((s) => fitLines(s.headline, rowTextW, 2, 56, 50, 12));
  const rowSize = Math.min(...rowFits.map((f) => f.size));
  const rowLines = rest.map((s) => fitLines(s.headline, rowTextW, 2, rowSize, rowSize, 12).lines);
  return (
    <div style={{ display: "flex", flexDirection: "column", width, height, background: E_BG }}>
      <EHeader digest={digest} size={42} height={HEAD} pad={SAFE} />
      <EPhoto photo={photos[0]} story={top} width={width} height={topPh} />
      <div style={{ display: "flex", alignItems: "baseline", gap: 16, padding: `22px ${SAFE}px 0`, height: topBlock }}>
        <div style={{ display: "flex", fontSize: num, fontWeight: 900, color: E_RED, width: num, flexShrink: 0 }}>1</div>
        <Lines lines={topText.lines} size={topText.size} />
      </div>
      {rest.map((story, i) => (
        <div key={story.slug} style={{ display: "flex", height: rowH, marginTop: GAP, alignItems: "center", gap: 32 }}>
          <EPhoto photo={photos[i + 1]} story={story} width={thumbW} height={rowH} />
          <div style={{ display: "flex", alignItems: "baseline", gap: 14, width: rowTextW + rowNum + 14 }}>
            <div style={{ display: "flex", fontSize: rowNum, fontWeight: 900, color: E_RED, width: rowNum, flexShrink: 0 }}>{i + 2}</div>
            <Lines lines={rowLines[i]} size={rowSize} />
          </div>
        </div>
      ))}
    </div>
  );
}

const DESIGNS = { A: DesignA, B: DesignB, B1: DesignB1, B2: DesignB2, C: DesignC, D1: DesignD1, D2: DesignD2, D3: DesignD3, D4: DesignD4, E1: designE(1), E2: DesignE2 } as const;

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
