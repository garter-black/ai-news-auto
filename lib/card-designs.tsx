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
export type CardDesign = "a" | "b" | "c" | "p1" | "p2" | "p3" | "p4";

export const CARD_SIZES: Record<CardDesign, { width: number; height: number }> = {
  a: { width: 1200, height: 675 },
  b: { width: 1080, height: 1080 },
  c: { width: 1200, height: 675 },
  p1: { width: 1200, height: 675 },
  p2: { width: 1200, height: 675 },
  p3: { width: 1200, height: 675 },
  p4: { width: 1200, height: 820 },
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
    for (const piece of phrase.split(/(?<= )/)) {
      if (emWidth(piece) <= max) {
        out.push(piece);
        continue;
      }
      // まだ長い（「電動アシストマウンテンバイクを」など）ときは、英字・漢字・カタカナのまとまりの境目で分ける
      out.push(...(piece.match(/[\x00-\x7f]+|[\u4e00-\u9fff々〆ヶ]+[\u3041-\u309f]*|[\u30a0-\u30ff]+[\u3041-\u309f]*|[^\x00-\x7f]/g) ?? [piece]));
    }
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
    // 2行目が1文字だけになる組み方は避ける
    const cost = Math.abs(emWidth(a) - emWidth(b)) + (b.length <= 1 ? 100 : 0);
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
  return lines.length <= maxLines && lines[lines.length - 1].length > 1 ? lines : null;
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
  // 最後の手段：文節を後ろから1つずつ落として「…」を付け、最小の文字で入るまで縮める（単語の途中では切らない）
  const phrases = phrasesOf(text);
  for (let keep = phrases.length - 1; keep >= 1; keep--) {
    const short = `${phrases.slice(0, keep).join("").replace(/[、。・\s]+$/, "")}…`;
    for (let n = 1; n <= maxLines; n++) {
      const lines = layoutLines(short, min, width, n);
      if (lines && lines.length === n) return { lines, size: min };
    }
  }
  return { lines: [phrases[0] ?? text], size: min };
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
function Lines({ lines, size, lineHeight = 1.24, color = INK, red = RED }: { lines: string[]; size: number; lineHeight?: number; color?: string; red?: string }) {
  let done = false;
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {lines.map((line, i) => {
        const hit = done ? null : numberSpan(line);
        if (hit) done = true;
        return (
          <div key={i} style={{ display: "flex", whiteSpace: "nowrap", fontSize: size, fontWeight: 900, lineHeight, color, letterSpacing: -0.5 }}>
            {hit ? (
              <>
                <span>{line.slice(0, hit[0])}</span>
                <span style={{ color: red }}>{line.slice(hit[0], hit[1])}</span>
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

const PHOTO_BOX: Record<"a" | "b" | "c", { width: number; height: number }> = {
  a: { width: 1200, height: 400 },
  b: { width: 1080, height: 520 },
  c: { width: 480, height: 575 },
};

export async function renderCardDesign(article: FullArticle, design: CardDesign): Promise<ImageResponse> {
  if (design.startsWith("p")) return renderPhotoFirst(article, design as PhotoDesign);
  const box = PHOTO_BOX[design as "a" | "b" | "c"];
  const credit = article.imageCredit;
  const centre = credit?.kind === "figure" || credit?.imageKind === "logo";
  const [photo, fontData] = await Promise.all([photoBox(article.thumbnail, box.width, box.height, centre), loadFonts()]);
  const { width, height } = CARD_SIZES[design];
  const body =
    design === "a" ? <DesignA article={article} photo={photo} /> : design === "b" ? <DesignB article={article} photo={photo} /> : <DesignC article={article} photo={photo} />;
  return new ImageResponse(body, { width, height, fonts: fontData });
}

/* ------------------------------------------------------------------------- *
 * 2026-11-03 案（白い帯なし）。写真の主役（article.imageFocus）を切らない・隠さないことを最優先にする。
 * 切り抜きは img の objectFit: cover + objectPosition で行う（sharp の無い Cloudflare でも同じ結果になる）。
 * ------------------------------------------------------------------------- */
export type PhotoDesign = "p1" | "p2" | "p3" | "p4";
const DEEP = "#0b1f45";
const DEFAULT_AR = 1200 / 630;
type Box = [number, number, number, number];
type Rect = { x: number; y: number; w: number; h: number };

/** 主役の枠（0〜1）。無い・写真が変わったときは写真全体を主役とみなす（＝切らない側に倒す） */
function focusOf(article: FullArticle): { box: Box; ar: number } {
  const f = article.imageFocus;
  const credit = article.imageCredit;
  if (credit?.kind === "figure" || credit?.imageKind === "logo") return { box: [0.08, 0.2, 0.92, 0.8], ar: f?.ar || DEFAULT_AR };
  if (!f || f.src !== article.thumbnail) return { box: [0, 0, 1, 1], ar: f?.ar || DEFAULT_AR };
  return { box: f.box, ar: f.ar || DEFAULT_AR };
}

/**
 * 写真を w×h の枠に cover で敷いたときに、主役が丸ごと入るずらし方。
 * 入れば objectPosition（%）と、枠の中での主役の位置（px）を返す。入らなければ null
 */
export function coverFit(ar: number, box: Box, w: number, h: number): { pos: string; subject: Rect } | null {
  const scale = Math.max(w / ar, h);
  const rw = ar * scale;
  const rh = scale;
  const place = (lo: number, hi: number, size: number, frame: number): number | null => {
    const a = lo * size;
    const b = hi * size;
    if (b - a > frame + 0.5) return null;
    const free = size - frame;
    if (free <= 0) return 0;
    const centre = (a + b) / 2 - frame / 2;
    return Math.min(Math.max(centre, b - frame, 0), Math.min(a, free));
  };
  const ox = place(box[0], box[2], rw, w);
  const oy = place(box[1], box[3], rh, h);
  if (ox === null || oy === null) return null;
  const px = rw - w > 0.5 ? (ox / (rw - w)) * 100 : 50;
  const py = rh - h > 0.5 ? (oy / (rh - h)) * 100 : 50;
  return {
    pos: `${px.toFixed(1)}% ${py.toFixed(1)}%`,
    subject: { x: box[0] * rw - ox, y: box[1] * rh - oy, w: (box[2] - box[0]) * rw, h: (box[3] - box[1]) * rh },
  };
}

/** 写真を w×h に収める（切らない）ときの大きさと位置 */
function containRect(ar: number, w: number, h: number): Rect {
  const pw = Math.min(w, h * ar);
  const ph = pw / ar;
  return { x: (w - pw) / 2, y: (h - ph) / 2, w: pw, h: ph };
}

function overlap(a: Rect, b: Rect): number {
  const x = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const y = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return x * y;
}

/** 写真（切り抜かない元の比率のまま）を data URL に。sharp が無ければ元の URL */
async function photoSource(url: string): Promise<string | null> {
  const photo = await fetchPhoto(url).catch(() => null);
  if (!photo) return /^https:\/\//.test(url) && !(await sharpWorks()) ? url : null;
  try {
    const jpeg = await sharp(photo).resize({ width: 1400, withoutEnlargement: true }).jpeg({ quality: 86 }).toBuffer();
    return `data:image/jpeg;base64,${jpeg.toString("base64")}`;
  } catch {
    return null;
  }
}

function Img({ src, rect, pos, fit }: { src: string; rect: Rect; pos?: string; fit: "cover" | "contain" }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={Math.round(rect.w)}
      height={Math.round(rect.h)}
      style={{ position: "absolute", left: Math.round(rect.x), top: Math.round(rect.y), width: Math.round(rect.w), height: Math.round(rect.h), objectFit: fit, objectPosition: pos ?? "50% 50%" }}
    />
  );
}

/** 小さなロゴ札（ネイビーの角丸）＋ニュース／速報 */
function CornerTag({ article, corner }: { article: FullArticle; corner: "tl" | "tr" | "bl" | "br" }) {
  const label = labelOf(article);
  const m = 26;
  const pos = { ...(corner[0] === "t" ? { top: m } : { bottom: m }), ...(corner[1] === "l" ? { left: m } : { right: m }) };
  return (
    <div style={{ position: "absolute", ...pos, display: "flex", alignItems: "center", gap: 12, padding: "10px 16px 11px", borderRadius: 12, background: "rgba(11,31,69,0.9)" }}>
      <Logo size={26} color={PAPER} />
      <div style={{ display: "flex", padding: "1px 10px", borderRadius: 5, background: label === "速報" ? RED : "rgba(255,255,255,0.16)", color: PAPER, fontSize: 20, fontWeight: 700 }}>{label}</div>
    </div>
  );
}

const TAG_SIZE = { w: 330, h: 70 };
/** 主役にかからない角（全部かかるなら、かかりがいちばん少ない角） */
function freeCorner(subject: Rect, w: number, h: number): "tl" | "tr" | "bl" | "br" {
  const m = 26;
  const rects: Record<"tl" | "tr" | "bl" | "br", Rect> = {
    tl: { x: m, y: m, ...TAG_SIZE },
    tr: { x: w - m - TAG_SIZE.w, y: m, ...TAG_SIZE },
    bl: { x: m, y: h - m - TAG_SIZE.h, ...TAG_SIZE },
    br: { x: w - m - TAG_SIZE.w, y: h - m - TAG_SIZE.h, ...TAG_SIZE },
  };
  return (Object.keys(rects) as ("tl" | "tr" | "bl" | "br")[]).reduce((best, k) => (overlap(rects[k], subject) < overlap(rects[best], subject) ? k : best), "tl");
}

/** 1: 写真だけ。主役を切らない位置で 16:9 いっぱいに。入らなければ写真全体をネイビーの地に収める */
function P1({ article, src, focus }: { article: FullArticle; src: string | null; focus: { box: Box; ar: number } }) {
  const { width, height } = CARD_SIZES.p1;
  const fit = coverFit(focus.ar, focus.box, width, height);
  const frame = { x: 0, y: 0, w: width, h: height };
  const inner = fit ? frame : containRect(focus.ar, width, height);
  const subject = fit ? fit.subject : { x: inner.x + focus.box[0] * inner.w, y: inner.y + focus.box[1] * inner.h, w: (focus.box[2] - focus.box[0]) * inner.w, h: (focus.box[3] - focus.box[1]) * inner.h };
  return (
    <div style={{ width, height, display: "flex", position: "relative", background: DEEP, fontFamily: "Noto" }}>
      {src ? <Img src={src} rect={inner} pos={fit?.pos} fit={fit ? "cover" : "contain"} /> : <NoPhoto width={width} height={height} name={sourceOf(article)} />}
      <CornerTag article={article} corner={freeCorner(subject, width, height)} />
    </div>
  );
}

/** 2: 写真いっぱい＋主役の反対側だけにうすい影と白い見出し（2行まで）。置ける場所が無い写真は写真だけ */
function P2({ article, src, focus }: { article: FullArticle; src: string | null; focus: { box: Box; ar: number } }) {
  const { width, height } = CARD_SIZES.p2;
  const fit = coverFit(focus.ar, focus.box, width, height);
  if (!fit || !src) return <P1 article={article} src={src} focus={focus} />;
  // 枠の余白（image_focus.py の MARGIN=5%）の分だけ内側を主役とみなす。影は主役の本体にはかけない
  const mx = fit.subject.w * 0.045;
  const my = fit.subject.h * 0.045;
  const s = { x: fit.subject.x + mx, y: fit.subject.y + my, w: fit.subject.w - mx * 2, h: fit.subject.h - my * 2 };
  const pad = 40;
  const headline = cardHeadline(article);
  // 候補：下いっぱい・上いっぱい・右半分・左半分。主役にかからず、いちばん広いところ
  const free = {
    bottom: { x: 0, y: s.y + s.h + 8, w: width, h: height - (s.y + s.h + 8) },
    top: { x: 0, y: 0, w: width, h: s.y - 8 },
    right: { x: s.x + s.w + 8, y: 0, w: width - (s.x + s.w + 8), h: height },
    left: { x: 0, y: 0, w: s.x - 8, h: height },
  };
  type Side = keyof typeof free;
  const vertical = (side: Side) => side === "bottom" || side === "top";
  let best: { side: Side; fit: { lines: string[]; size: number } } | null = null;
  for (const side of ["bottom", "top", "right", "left"] as Side[]) {
    const r = free[side];
    if (r.w < 300 || r.h < 130) continue;
    const textW = r.w - pad * 2;
    const maxSize = Math.min(64, Math.floor((r.h - pad * 1.2) / 2.5));
    if (maxSize < 42) continue;
    const t = layoutAt(headline, textW, 2, maxSize, 42) ?? layoutAt(compactTail(headline), textW, 2, maxSize, 42) ?? (vertical(side) ? null : layoutAt(compactTail(headline), textW, 3, maxSize, 42));
    if (t && (!best || t.size > best.fit.size)) best = { side, fit: t };
  }
  if (!best) return <P1 article={article} src={src} focus={focus} />;
  const r = free[best.side];
  const dir = { bottom: "to top", top: "to bottom", right: "to left", left: "to right" }[best.side];
  const textH = best.fit.lines.length * best.fit.size * 1.2;
  const textBox: Rect = vertical(best.side)
    ? { x: pad, y: best.side === "bottom" ? height - pad - textH : pad + 70, w: width - pad * 2, h: textH }
    : { x: r.x + pad, y: (height - textH) / 2, w: r.w - pad * 2, h: textH };
  const corner = freeCorner({ x: Math.min(s.x, textBox.x), y: Math.min(s.y, textBox.y), w: Math.max(s.x + s.w, textBox.x + textBox.w) - Math.min(s.x, textBox.x), h: Math.max(s.y + s.h, textBox.y + textBox.h) - Math.min(s.y, textBox.y) }, width, height);
  return (
    <div style={{ width, height, display: "flex", position: "relative", background: DEEP, fontFamily: "Noto" }}>
      <Img src={src} rect={{ x: 0, y: 0, w: width, h: height }} pos={fit.pos} fit="cover" />
      <div style={{ position: "absolute", left: r.x, top: r.y, width: r.w, height: r.h, display: "flex", backgroundImage: `linear-gradient(${dir}, rgba(6,14,32,0.82) 0%, rgba(6,14,32,0.55) 55%, rgba(6,14,32,0) 100%)` }} />
      <div style={{ position: "absolute", left: textBox.x, top: textBox.y, display: "flex" }}>
        <Lines lines={best.fit.lines} size={best.fit.size} lineHeight={1.2} color={PAPER} red="#ff6a5f" />
      </div>
      <CornerTag article={article} corner={corner} />
    </div>
  );
}

/** 読点の後ろ（「Hondaが…を公開、独ROTWILDと共同開発」→ 前半が長いときの短い見出し） */
function compactTail(text: string): string {
  const parts = text.split(/[、，]/);
  return parts.length > 1 && parts[0].length >= 10 ? parts[0] : text;
}

/** 3: 左 57% に写真（主役が入れば枠いっぱい、入らなければ写真全体）、右の濃紺にタグ・見出し・ロゴ */
function P3({ article, src, focus }: { article: FullArticle; src: string | null; focus: { box: Box; ar: number } }) {
  const { width, height } = CARD_SIZES.p3;
  const pw = Math.round(width * 0.57);
  const fit = coverFit(focus.ar, focus.box, pw, height);
  const rect = fit ? { x: 0, y: 0, w: pw, h: height } : containRect(focus.ar, pw, height);
  const pad = 40;
  const textW = width - pw - pad * 2;
  const headline = cardHeadline(article);
  const t = layoutAt(headline, textW, 3, 56, 30) ?? fitLines(compactTail(headline), textW, 3, 56, 30);
  const label = labelOf(article);
  return (
    <div style={{ width, height, display: "flex", position: "relative", background: DEEP, fontFamily: "Noto" }}>
      {src ? <Img src={src} rect={rect} pos={fit?.pos} fit={fit ? "cover" : "contain"} /> : <NoPhoto width={pw} height={height} name={sourceOf(article)} />}
      <div style={{ position: "absolute", left: pw, top: 0, width: width - pw, height, display: "flex", flexDirection: "column", justifyContent: "space-between", padding: `${pad + 6}px ${pad}px` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ display: "flex", padding: "2px 14px", borderRadius: 6, background: label === "速報" ? RED : "rgba(255,255,255,0.14)", color: PAPER, fontSize: 24, fontWeight: 700 }}>{label}</div>
          <div style={{ display: "flex", fontSize: 24, fontWeight: 700, color: "rgba(255,255,255,0.7)" }}>{sourceOf(article)}</div>
        </div>
        <Lines lines={t.lines} size={t.size} lineHeight={1.25} color={PAPER} red="#ff6a5f" />
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
          <Logo size={30} color={PAPER} />
          <div style={{ display: "flex", fontSize: 20, fontWeight: 700, color: "rgba(255,255,255,0.55)" }}>{dateOf(article)}</div>
        </div>
      </div>
    </div>
  );
}

/** 4: 濃紺の額。写真は切らずに全体を見せ、下のキャプション帯に見出し2行・タグ・ロゴ（雑誌のグラビア風） */
function P4({ article, src, focus }: { article: FullArticle; src: string | null; focus: { box: Box; ar: number } }) {
  const { width, height } = CARD_SIZES.p4;
  const m = 24;
  const photoArea = { w: width - m * 2, h: Math.round((width - m * 2) / DEFAULT_AR) };
  const inner = containRect(focus.ar, photoArea.w, photoArea.h);
  const capTop = m + photoArea.h;
  const capH = height - capTop;
  const pad = 36;
  const right = 300;
  const textW = width - m * 2 - pad - right;
  const headline = cardHeadline(article);
  const t = layoutAt(headline, textW, 2, 50, 36) ?? fitLines(compactTail(headline), textW, 2, 50, 34);
  const label = labelOf(article);
  return (
    <div style={{ width, height, display: "flex", position: "relative", background: DEEP, fontFamily: "Noto" }}>
      <div style={{ position: "absolute", left: m, top: m, width: photoArea.w, height: photoArea.h, display: "flex", background: "#081733" }}>
        {src ? <Img src={src} rect={inner} fit="contain" /> : <NoPhoto width={photoArea.w} height={photoArea.h} name={sourceOf(article)} />}
      </div>
      <div style={{ position: "absolute", left: m, top: capTop, width: width - m * 2, height: capH, display: "flex", alignItems: "center" }}>
        <div style={{ width: 6, height: Math.round(t.lines.length * t.size * 1.22), display: "flex", background: label === "速報" ? RED : "#4f7cc4", marginLeft: 6, marginRight: pad - 12 }} />
        <div style={{ width: textW, display: "flex" }}>
          <Lines lines={t.lines} size={t.size} lineHeight={1.22} color={PAPER} red="#ff6a5f" />
        </div>
        <div style={{ width: right, display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 10 }}>
          <Logo size={30} color={PAPER} />
          <div style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 20, fontWeight: 700, color: "rgba(255,255,255,0.6)" }}>
            <div style={{ display: "flex", padding: "1px 10px", borderRadius: 5, background: label === "速報" ? RED : "rgba(255,255,255,0.14)", color: PAPER }}>{label}</div>
            {dateOf(article)}
          </div>
        </div>
      </div>
    </div>
  );
}

async function renderPhotoFirst(article: FullArticle, design: PhotoDesign): Promise<ImageResponse> {
  const [src, fontData] = await Promise.all([photoSource(article.thumbnail), loadFonts()]);
  const focus = focusOf(article);
  const { width, height } = CARD_SIZES[design];
  const Body = { p1: P1, p2: P2, p3: P3, p4: P4 }[design];
  return new ImageResponse(<Body article={article} src={src} focus={focus} />, { width, height, fonts: fontData });
}
