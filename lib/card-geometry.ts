/**
 * SNS カード（lib/card-designs.tsx）の配置の計算。画像を描かずに確かめられるよう、ここは計算だけにする
 * （scripts/check_card_crop.mjs が同じ関数で「主役が切れていない・ロゴや注記の下になっていない」を点検する）。
 * 座標の box は写真に対する 0〜1（scripts/image_focus.py の imageFocus.box）。
 */
export type Box = [number, number, number, number];
export type Rect = { x: number; y: number; w: number; h: number };
export type Corner = "tl" | "tr" | "bl" | "br";

export const CARD_W = 1200;
export const CARD_H = 675;
export const DEFAULT_AR = 1200 / 630;
/** 角に置く札（ロゴ＋ニュース／速報）と注記（画像はイメージ など）の大きさの上限と、端からの余白 */
export const TAG = { w: 290, h: 60 };
export const NOTE = { w: 560, h: 40 };
export const EDGE = 22;
/** imageFocus.box の外側の余白（scripts/image_focus.py の MARGIN）。切り抜きは余白ごと守り、札・注記は余白の内側（主役の本体）を避ける */
export const FOCUS_MARGIN = 0.05;

type Focusable = {
  thumbnail: string;
  imageFocus?: { box: Box; ar: number; src: string };
  imageCredit?: { kind?: string; imageKind?: string };
};

/** 主役の枠。無い・写真が変わったときは写真全体を主役とみなす（＝切らない側に倒す）。数字の図・ロゴは真ん中の帯 */
export function focusOf(article: Focusable): { box: Box; ar: number } {
  const f = article.imageFocus;
  const credit = article.imageCredit;
  if (credit?.kind === "figure" || credit?.imageKind === "logo") return { box: [0.08, 0.2, 0.92, 0.8], ar: f?.ar || DEFAULT_AR };
  if (!f || f.src !== article.thumbnail) return { box: [0, 0, 1, 1], ar: f?.ar || DEFAULT_AR };
  return { box: f.box, ar: f.ar || DEFAULT_AR };
}

/**
 * 写真を w×h の枠に cover で敷いたとき、主役が丸ごと入るずらし方。
 * 入れば objectPosition（%）と枠の中での主役の位置（px）、入らなければ null
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
export function containRect(ar: number, w: number, h: number): Rect {
  const pw = Math.min(w, h * ar);
  const ph = pw / ar;
  return { x: (w - pw) / 2, y: (h - ph) / 2, w: pw, h: ph };
}

export function overlap(a: Rect, b: Rect): number {
  const x = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
  const y = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
  return x * y;
}

export function cornerRect(corner: Corner, size: { w: number; h: number }, w = CARD_W, h = CARD_H): Rect {
  return {
    x: corner[1] === "l" ? EDGE : w - EDGE - size.w,
    y: corner[0] === "t" ? EDGE : h - EDGE - size.h,
    ...size,
  };
}

const CORNERS: Corner[] = ["tl", "tr", "bl", "br"];

/** 主役にかかりがいちばん少ない角（同じなら tl→tr→bl→br の順）。avoid の角は使わない */
export function freeCorner(subject: Rect, size: { w: number; h: number }, avoid?: Corner, w = CARD_W, h = CARD_H): Corner {
  const options = CORNERS.filter((c) => c !== avoid && !(avoid && c[0] === avoid[0] && size.w + TAG.w + EDGE * 3 > w));
  return options.reduce((best, c) => (overlap(cornerRect(c, size, w, h), subject) < overlap(cornerRect(best, size, w, h), subject) ? c : best), options[0]);
}

/** 写真の中の主役の位置（rect に写真を敷いたとき）と、その本体（余白を除く。写真の端に接している辺はそのまま） */
function subjectIn(box: Box, rect: Rect, fit: { subject: Rect } | null): { subject: Rect; core: Rect } {
  const subject: Rect = fit
    ? fit.subject
    : { x: rect.x + box[0] * rect.w, y: rect.y + box[1] * rect.h, w: (box[2] - box[0]) * rect.w, h: (box[3] - box[1]) * rect.h };
  const shrink = (v: number, edge: boolean, dir: 1 | -1) => (edge ? v : v + dir * FOCUS_MARGIN);
  const c: Box = [shrink(box[0], box[0] <= 0.001, 1), shrink(box[1], box[1] <= 0.001, 1), shrink(box[2], box[2] >= 0.999, -1), shrink(box[3], box[3] >= 0.999, -1)];
  const sx = subject.w / Math.max(box[2] - box[0], 1e-6);
  const sy = subject.h / Math.max(box[3] - box[1], 1e-6);
  return { subject, core: { x: subject.x + (c[0] - box[0]) * sx, y: subject.y + (c[1] - box[1]) * sy, w: (c[2] - c[0]) * sx, h: (c[3] - c[1]) * sy } };
}

/** 札を置く上の余白（inset のとき）。札は EDGE から TAG.h の高さ */
export const INSET_TOP = EDGE + TAG.h + 8;

/**
 * 写真だけのカード（p1）の配置。
 *  - cover: 主役を切らない位置で 16:9 いっぱい。札・注記は主役の本体にかからない角へ
 *  - contain: 主役が 16:9 に収まらない写真は、写真全体を濃紺の地に収める
 *  - inset: どの角も主役にかかる写真は、写真全体を少し小さくして下に寄せ、上の濃紺の余白に札と注記を置く（写真の上には何も載せない）
 */
export function photoOnlyLayout(article: Focusable, hasNote: boolean) {
  const focus = focusOf(article);
  const fit = coverFit(focus.ar, focus.box, CARD_W, CARD_H);
  const inner = fit ? { x: 0, y: 0, w: CARD_W, h: CARD_H } : containRect(focus.ar, CARD_W, CARD_H);
  const { subject, core } = subjectIn(focus.box, inner, fit);
  const tag = freeCorner(core, TAG);
  const note = hasNote ? freeCorner(core, NOTE, tag) : null;
  const clear = overlap(cornerRect(tag, TAG), core) === 0 && (!note || overlap(cornerRect(note, NOTE), core) === 0);
  if (clear) return { mode: fit ? ("cover" as const) : ("contain" as const), fit, inner, subject, core, tag, note };
  const box = containRect(focus.ar, CARD_W, CARD_H - INSET_TOP);
  const insetRect: Rect = { x: box.x, y: INSET_TOP, w: box.w, h: box.h };
  const placed = subjectIn(focus.box, insetRect, null);
  return { mode: "inset" as const, fit: null, inner: insetRect, ...placed, tag: "tl" as Corner, note: hasNote ? ("tr" as Corner) : null };
}
