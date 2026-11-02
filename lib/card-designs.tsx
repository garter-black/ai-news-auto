import { promises as fs } from "@/lib/content-fs";
import path from "node:path";
import { loadDefaultJapaneseParser } from "budoux";
import { ImageResponse } from "next/og";
import sharp from "sharp";
import type { FullArticle } from "@/lib/articles";
import { companyOf } from "@/lib/companies";
import { fetchPhoto } from "@/lib/cover";

/**
 * SNS 投稿に添えるカード（2026-11 案）。縦長の「見出し＋要点3つ」をやめ、文字を見出し1本に絞る。
 *
 * 伸びているニュース系アカウント（日経・Yahoo!ニュース・ライブドアニュースなど）は、横長（16:9 前後）の写真か
 * リンクカードに、本文は見出し1〜3行だけ。タイムラインで縦に場所を取らず、本文と画像で同じことを繰り返さない。
 *
 * 色はサイトと同じ（白地・ネイビー・赤は1か所だけ＝見出しの数字）。書体は Noto Sans JP、ロゴだけ Dela Gothic One。
 * どの見出し・どの写真でも手直しなしで描けるよう、見出しは文字数から大きさを決め、写真は sharp で枠に合わせて切り抜くだけ。
 *
 * A: 16:9 写真＋白い見出し帯   B: 1:1 ネイビーの題字＋写真＋見出し   C: 16:9 見出しが主役・写真は右側いっぱい
 */
export type CardDesign = "a" | "b" | "c";

export const CARD_SIZES: Record<CardDesign, { width: number; height: number }> = {
  a: { width: 1200, height: 675 },
  b: { width: 1080, height: 1080 },
  c: { width: 1200, height: 675 },
};

const INK = "#0b1a33";
const NAVY = "#1d4a8f";
const RED = "#c8221a";
const GRAY = "#5b6475";
const PAPER = "#ffffff";

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
  for (const char of text) total += /[\u0000-ÿ]/.test(char) ? 0.7 : 1;
  return total;
}

/** budoux の文節。1文字だけの助詞・記号（「の」「、」など）は前の文節にくっつけ、行頭にぽつんと残らないようにする */
function phrasesOf(text: string): string[] {
  const out: string[] = [];
  for (const phrase of parser.parse(text)) {
    if (out.length && /^[\u3041-\u309f、。」』）・ー]{1,2}$/.test(phrase)) out[out.length - 1] += phrase;
    else out.push(phrase);
  }
  return out;
}

/**
 * カードの見出し。記事タイトルが長いときは、最初の区切り（空白・－・｜）までの前半を使う
 * （例:「2026年10月の「円安」倒産は6件、52カ月連続で発生 1-10月累計は…」→ 前半だけ）。前半が短すぎるときは全文。
 */
export function cardHeadline(article: FullArticle): string {
  const title = (article.title || article.seoTitle || "").trim();
  // 区切りは全角の空白・－・｜と、日本語のすぐ後ろの半角空白だけ（「Agent Compute Engine」の中の空白では切らない）
  const head = title.split(/(?<=[^\x00-\x7f])[ 　]|[　－―—｜|]/)[0].trim();
  const balanced = (head.match(/[『「（(]/g) ?? []).length === (head.match(/[』」）)]/g) ?? []).length;
  let text = head.length >= 12 && head.length < title.length && balanced ? head : title;
  // まだ長いときは、英字だけの製品名のかっこ（『Agent Compute Engine』など）を外す。製品名は投稿の本文に書く
  if (text.length > 34) text = text.replace(/[『「][\x20-\x7e]+[』」]/g, "").replace(/\s+/g, " ").trim();
  return text;
}

/** 折り返しの単位。文節が1行より長いときは、半角空白の後ろ、それでも長ければ1文字ずつに分ける */
function wrapTokens(text: string, max: number): string[] {
  const out: string[] = [];
  for (const phrase of phrasesOf(text)) {
    if (emWidth(phrase) <= max) {
      out.push(phrase);
      continue;
    }
    // 英字と日本語の境目・半角空白の後ろで分ける（1文字ずつには分けない。単語の途中で切らないため）
    for (const piece of phrase.split(/(?<= )/)) out.push(...(piece.match(/[\x00-\x7f]+|[^\x00-\x7f]+/g) ?? [piece]));
  }
  return out;
}

/** 行を組む（単語・文節の途中で切らない）。2行なら左右の長さがそろう切れ目を選ぶ。入らなければ null */
function layoutLines(text: string, size: number, width: number, maxLines: number): string[] | null {
  const max = (width * 0.95) / size;
  if (emWidth(text) <= max) return [text];
  if (maxLines < 2) return null;
  const tokens = wrapTokens(text, max);
  if (tokens.some((t) => emWidth(t.trim()) > max)) return null;
  let best: string[] | null = null;
  let bestCost = Infinity;
  for (let i = 1; i < tokens.length; i++) {
    const a = tokens.slice(0, i).join("").trim();
    const b = tokens.slice(i).join("").trim();
    if (emWidth(a) > max || emWidth(b) > max) continue;
    // 2行目が1〜2文字だけになる組み方は避ける
    const cost = Math.abs(emWidth(a) - emWidth(b)) + (b.length <= 2 ? 100 : 0);
    if (cost < bestCost) {
      bestCost = cost;
      best = [a, b];
    }
  }
  if (best && bestCost < 100) return best;
  if (maxLines < 3) return null;
  const lines: string[] = [];
  let line = "";
  for (const t of tokens) {
    if (line && emWidth(line + t) > max) {
      lines.push(line.trim());
      line = t;
    } else line += t;
  }
  if (line.trim()) lines.push(line.trim());
  return lines.length <= maxLines && lines[lines.length - 1].length > 2 ? lines : null;
}

/** 見出しを短くする候補（読点の後ろ・かぎかっこの中身など）。最後の手段だけ「…」 */
function compactCandidates(text: string): string[] {
  const out = [text];
  const comma = text.search(/[、，]/);
  if (comma > 0 && text.length - comma > 10) out.push(text.slice(comma + 1));
  const noParen = text.replace(/[（(][^）)]*[）)]/g, "");
  if (noParen !== text) out.push(noParen);
  return out;
}

/** いちばん大きい文字で行を組む（枠を埋める）。最小でも入らなければ見出しを短くし、最後は「…」 */
function fitLines(text: string, width: number, maxLines: number, max: number, min: number): { lines: string[]; size: number } {
  for (const candidate of compactCandidates(text)) {
    for (let size = max; size >= min; size -= 2) {
      for (let n = 1; n <= maxLines; n++) {
        const lines = layoutLines(candidate, size, width, n);
        if (lines && lines.length === n) return { lines, size };
      }
    }
  }
  const limit = ((width * 0.95) / min) * maxLines - 2;
  let out = "";
  for (const phrase of phrasesOf(text)) {
    if (emWidth(out + phrase) > limit) break;
    out += phrase;
  }
  return fitLines(`${out.replace(/[、。・\s]+$/, "")}…`, width, maxLines, min, min);
}

/** 決まった行数の範囲で、いちばん大きく組めるサイズ（見出しは短くしない）。無ければ null */
function layoutAt(text: string, width: number, maxLines: number, max: number, min: number): { lines: string[]; size: number } | null {
  for (let size = max; size >= min; size -= 2) {
    for (let n = 1; n <= maxLines; n++) {
      const lines = layoutLines(text, size, width, n);
      if (lines && lines.length === n) return { lines, size };
    }
  }
  return null;
}

/** 組んだ行をそのまま描く（行ごとに折り返しなし）。最初の数字だけ赤 */
function Lines({ lines, size, lineHeight = 1.24 }: { lines: string[]; size: number; lineHeight?: number }) {
  let done = false;
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {lines.map((line, i) => {
        const hit = done ? null : numberSpan(line);
        if (hit) done = true;
        return (
          <div key={i} style={{ display: "flex", whiteSpace: "nowrap", fontSize: size, fontWeight: 900, lineHeight, color: INK, letterSpacing: -0.5 }}>
            {hit ? (
              <>
                <span>{line.slice(0, hit[0])}</span>
                <span style={{ color: RED }}>{line.slice(hit[0], hit[1])}</span>
                <span>{line.slice(hit[1])}</span>
              </>
            ) : (
              line
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 見出しの中で赤くする数字（最初の「数字＋単位」。年・月・日は除く） */
const NUMBER_RE = /[0-9][0-9,.]*(?:万|億|兆)?(?:円|ドル|件|％|%|口座|人|社|倍|カ月|か月|ヶ月|分|秒|時間|台|本|店|位|GB|TB|万|億|兆)/;
function numberSpan(text: string): [number, number] | null {
  const pattern = new RegExp(NUMBER_RE.source, "g");
  for (const match of text.matchAll(pattern)) {
    return [match.index ?? 0, (match.index ?? 0) + match[0].length];
  }
  return null;
}

function Logo({ size, color }: { size: number; color: string }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: Math.round(size * 0.18) }}>
      <div style={{ display: "flex", fontFamily: "Dela", fontSize: size, color, lineHeight: 1.1 }}>サイソクニュース</div>
      <div style={{ width: Math.round(size * 0.32), height: Math.round(size * 0.32), borderRadius: size, background: RED, marginTop: Math.round(size * 0.08) }} />
    </div>
  );
}

function labelOf(article: FullArticle): string {
  if (article.type === "roundup") return "まとめ";
  if (article.breaking) return "速報";
  return "ニュース";
}

function sourceOf(article: FullArticle): string {
  const company = companyOf(article);
  if (company.slug !== "other") return company.name;
  const name = (article.sourceType === "web" ? article.sourceName || article.channel : "") || "";
  return name.replace(/\s*(公式ブログ|プレスリリース|ニュースリリース|公式サイト)$/, "").slice(0, 18);
}

function dateOf(article: FullArticle): string {
  const d = new Date(article.publishedAt || article.generatedAt);
  if (Number.isNaN(d.getTime())) return "";
  const jst = new Date(d.getTime() + 9 * 3600 * 1000);
  return `${jst.getUTCFullYear()}.${jst.getUTCMonth() + 1}.${jst.getUTCDate()}`;
}

/**
 * 写真を枠ぴったりに切り抜いた data URL（暗くしない）。sharp が無い Cloudflare Workers では元の URL をそのまま使う（CSS の cover で切り抜く）。
 * 数字の図・ロゴ（scripts/images.py が真ん中の帯に描く）は、目立つ部分ではなく真ん中で切り抜く
 */
async function photoBox(url: string, width: number, height: number, centre = false): Promise<string | null> {
  const photo = await fetchPhoto(url).catch(() => null);
  if (!photo) return /^https:\/\//.test(url) && !(await sharpWorks()) ? url : null;
  try {
    const position = centre ? "centre" : sharp.strategy.attention;
    const jpeg = await sharp(photo).resize(width, height, { fit: "cover", position }).jpeg({ quality: 86 }).toBuffer();
    return `data:image/jpeg;base64,${jpeg.toString("base64")}`;
  } catch {
    return null;
  }
}

let sharpOk: Promise<boolean> | null = null;
function sharpWorks(): Promise<boolean> {
  sharpOk ??= sharp({ create: { width: 1, height: 1, channels: 3, background: "#fff" } }).png().toBuffer().then(() => true, () => false);
  return sharpOk;
}

/** 写真が無いときの枠：ネイビーの地に発表元の名前をうすく */
function NoPhoto({ width, height, name }: { width: number; height: number; name: string }) {
  const size = Math.min(150, Math.floor((width * 0.86) / Math.max(emWidth(name || "NEWS"), 1)));
  return (
    <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center", background: NAVY }}>
      <div style={{ display: "flex", fontSize: size, fontWeight: 900, color: "rgba(255,255,255,0.18)", letterSpacing: -2 }}>{name || "NEWS"}</div>
    </div>
  );
}

function Photo({ src, width, height, name, radius = 0 }: { src: string | null; width: number; height: number; name: string; radius?: number }) {
  return (
    <div style={{ width, height, display: "flex", overflow: "hidden", borderRadius: radius }}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" width={width} height={height} style={{ objectFit: "cover" }} />
      ) : (
        <NoPhoto width={width} height={height} name={name} />
      )}
    </div>
  );
}

function Chip({ text, red, size }: { text: string; red: boolean; size: number }) {
  return (
    <div style={{ display: "flex", padding: `${Math.round(size * 0.12)}px ${Math.round(size * 0.55)}px`, borderRadius: 6, background: red ? RED : NAVY, color: PAPER, fontSize: size, fontWeight: 700 }}>
      {text}
    </div>
  );
}

/** A: 16:9。上に写真（そのまま）、下の白い帯にラベルと大きな見出し2行 */
function DesignA({ article, photo }: { article: FullArticle; photo: string | null }) {
  const { width, height } = CARD_SIZES.a;
  const pad = 60;
  const headline = cardHeadline(article);
  // 2行で大きく組めればそれを使い、無理なときだけ3行に
  const two = layoutAt(headline, width - pad * 2, 2, 76, 50);
  const fit = two ?? fitLines(headline, width - pad * 2, 3, 58, 44);
  const label = labelOf(article);
  const source = sourceOf(article);
  return (
    <div style={{ width, height, display: "flex", flexDirection: "column", background: PAPER, fontFamily: "Noto", position: "relative" }}>
      <Photo src={photo} width={width} height={PHOTO_BOX.a.height} name={source} />
      <div style={{ position: "absolute", left: 0, top: 0, display: "flex", padding: "18px 28px 20px", borderBottomRightRadius: 18, background: INK }}>
        <Logo size={40} color={PAPER} />
      </div>
      <div style={{ height: 8, display: "flex", background: NAVY }} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", padding: `0 ${pad}px`, gap: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <Chip text={label} red={label === "速報"} size={28} />
          {source && <div style={{ display: "flex", fontSize: 30, fontWeight: 700, color: GRAY }}>{source}</div>}
        </div>
        <Lines lines={fit.lines} size={fit.size} lineHeight={1.2} />
      </div>
    </div>
  );
}

/** B: 1:1。ネイビーの題字、写真、白地に大きな見出し3行まで */
function DesignB({ article, photo }: { article: FullArticle; photo: string | null }) {
  const { width, height } = CARD_SIZES.b;
  const mast = 120;
  const pad = 64;
  const fit = fitLines(cardHeadline(article), width - pad * 2, 3, 80, 50);
  const label = labelOf(article);
  const source = sourceOf(article);
  return (
    <div style={{ width, height, display: "flex", flexDirection: "column", background: PAPER, fontFamily: "Noto" }}>
      <div style={{ height: mast, display: "flex", alignItems: "center", justifyContent: "space-between", padding: `0 ${pad}px`, background: INK }}>
        <Logo size={50} color={PAPER} />
        <div style={{ display: "flex", fontSize: 30, fontWeight: 700, color: "rgba(255,255,255,0.75)" }}>{dateOf(article)}</div>
      </div>
      <Photo src={photo} width={width} height={PHOTO_BOX.b.height} name={source} />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", padding: `0 ${pad}px`, gap: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <Chip text={label} red={label === "速報"} size={30} />
          {source && <div style={{ display: "flex", fontSize: 32, fontWeight: 700, color: GRAY }}>{source}</div>}
        </div>
        <Lines lines={fit.lines} size={fit.size} lineHeight={1.2} />
      </div>
    </div>
  );
}

/** C: 16:9。上にネイビーの題字、左に大きな見出し3行まで、右半分は写真（端まで） */
function DesignC({ article, photo }: { article: FullArticle; photo: string | null }) {
  const { width, height } = CARD_SIZES.c;
  const mast = 100;
  const pad = 56;
  const side = PHOTO_BOX.c.width;
  const textWidth = width - side - pad * 2;
  const fit = fitLines(cardHeadline(article), textWidth, 4, 76, 44);
  const label = labelOf(article);
  const source = sourceOf(article);
  return (
    <div style={{ width, height, display: "flex", flexDirection: "column", background: PAPER, fontFamily: "Noto" }}>
      <div style={{ height: mast, display: "flex", alignItems: "center", justifyContent: "space-between", padding: `0 ${pad}px`, background: INK }}>
        <Logo size={44} color={PAPER} />
        <div style={{ display: "flex", fontSize: 28, fontWeight: 700, color: "rgba(255,255,255,0.75)" }}>{dateOf(article)}</div>
      </div>
      <div style={{ flex: 1, display: "flex" }}>
        <div style={{ width: textWidth + pad * 2, display: "flex", flexDirection: "column", justifyContent: "center", padding: `0 ${pad}px`, gap: 16, borderLeft: `0px solid ${NAVY}` }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <Chip text={label} red={label === "速報"} size={28} />
            {source && <div style={{ display: "flex", fontSize: 28, fontWeight: 700, color: GRAY }}>{source}</div>}
          </div>
          <Lines lines={fit.lines} size={fit.size} lineHeight={1.22} />
        </div>
        <Photo src={photo} width={side} height={PHOTO_BOX.c.height} name={source} />
      </div>
    </div>
  );
}

const PHOTO_BOX: Record<CardDesign, { width: number; height: number }> = {
  a: { width: 1200, height: 400 },
  b: { width: 1080, height: 520 },
  c: { width: 480, height: 575 },
};

export async function renderCardDesign(article: FullArticle, design: CardDesign): Promise<ImageResponse> {
  const box = PHOTO_BOX[design];
  const credit = article.imageCredit;
  const centre = credit?.kind === "figure" || credit?.imageKind === "logo";
  const [photo, fontData] = await Promise.all([photoBox(article.thumbnail, box.width, box.height, centre), loadFonts()]);
  const { width, height } = CARD_SIZES[design];
  const body =
    design === "a" ? <DesignA article={article} photo={photo} /> : design === "b" ? <DesignB article={article} photo={photo} /> : <DesignC article={article} photo={photo} />;
  return new ImageResponse(body, { width, height, fonts: fontData });
}
