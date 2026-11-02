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

/** 主役の本体（imageFocus.box から余白 FOCUS_MARGIN を除く。写真の端に接している辺はそのまま） */
export function coreOf(box: Box): Box {
  const shrink = (v: number, edge: boolean, dir: 1 | -1) => (edge ? v : v + dir * FOCUS_MARGIN);
  return [shrink(box[0], box[0] <= 0.001, 1), shrink(box[1], box[1] <= 0.001, 1), shrink(box[2], box[2] >= 0.999, -1), shrink(box[3], box[3] >= 0.999, -1)];
}

/** 切り抜きで守る範囲：主役の本体＋少しの余白（背景はここまで削ってよい） */
export const KEEP_MARGIN = 0.015;
function keepOf(box: Box): Box {
  const c = coreOf(box);
  return [Math.max(0, c[0] - KEEP_MARGIN), Math.max(0, c[1] - KEEP_MARGIN), Math.min(1, c[2] + KEEP_MARGIN), Math.min(1, c[3] + KEEP_MARGIN)];
}

/** 16:9 に敷いたとき主役がはみ出すなら、はみ出しが写真の TRIM_MAX 以内に限り、主役の枠を中央へ縮める（それ以上なら縮めない） */
export const TRIM_MAX = 0.1;
function trimTo(keep: Box, ar: number): Box {
  const scale = Math.max(CARD_W / ar, CARD_H);
  const fw = CARD_W / (ar * scale); // 枠に入る写真の幅の割合
  const fh = CARD_H / scale;
  const shrinkAxis = (lo: number, hi: number, frame: number): [number, number] => {
    const over = hi - lo - frame;
    if (over <= 0 || over > TRIM_MAX) return [lo, hi];
    return [lo + over / 2 + 0.001, hi - over / 2 - 0.001];
  };
  const [x0, x1] = shrinkAxis(keep[0], keep[2], fw);
  const [y0, y1] = shrinkAxis(keep[1], keep[3], fh);
  return [x0, y0, x1, y1];
}

/** 写真（0〜1 の座標）の枠を、写真を rect に敷いたときの位置（px）に */
function mapBox(box: Box, rect: Rect): Rect {
  return { x: rect.x + box[0] * rect.w, y: rect.y + box[1] * rect.h, w: (box[2] - box[0]) * rect.w, h: (box[3] - box[1]) * rect.h };
}

/** 札の小さい版（どの角も主役にかかるとき。ロゴだけ・半透明） */
export const TAG_SMALL = { w: 200, h: 44 };
export const NOTE_SMALL = { w: 300, h: 30 };

/**
 * 写真だけのカード（p1）の配置。写真が主役なので、できるだけ 16:9 いっぱいに敷く。
 *  - cover: 主役の本体＋少しの余白が 16:9 に入れば、背景を削って枠いっぱいに（ほとんどの写真）
 *  - contain: 主役がどうしても 16:9 に入らない（縦に長い等）ときだけ、写真全体を収め、まわりは同じ写真をぼかして暗くしたもので埋める
 * 札（ロゴ＋ニュース／速報）は主役にかからない角へ。どの角もかかるなら小さい半透明のロゴだけ、それでもかかるなら札は出さない。
 * 写真を札のために小さくすることはしない。注記（AI生成など）は小さく、いちばんかからない角に必ず出す。
 */
export function photoOnlyLayout(article: Focusable, hasNote: boolean) {
  const focus = focusOf(article);
  const keep = keepOf(focus.box);
  const scale = Math.max(CARD_W / focus.ar, CARD_H);
  // 主役が枠より少しだけ大きい（端まで続く机・街並みなど）ときは、はみ出す分を両側から均等に削って敷く（TRIM_MAX まで）
  const fitted = coverFit(focus.ar, keep, CARD_W, CARD_H) ?? coverFit(focus.ar, trimTo(keep, focus.ar), CARD_W, CARD_H);
  let inner: Rect;
  let photoRect: Rect; // 写真全体が置かれる位置（cover のときは枠の外にはみ出す）
  if (fitted) {
    inner = { x: 0, y: 0, w: CARD_W, h: CARD_H };
    const rw = focus.ar * scale;
    const rh = scale;
    const [px, py] = fitted.pos.split(" ").map((v) => parseFloat(v) / 100);
    photoRect = { x: -(rw - CARD_W) * px, y: -(rh - CARD_H) * py, w: rw, h: rh };
  } else {
    inner = containRect(focus.ar, CARD_W, CARD_H);
    photoRect = inner;
  }
  const subject = mapBox(focus.box, photoRect);
  const core = mapBox(coreOf(focus.box), photoRect);
  const corners: Corner[] = ["tl", "tr", "bl", "br"];
  const clearOf = (size: { w: number; h: number }, avoid?: Corner) => corners.find((c) => c !== avoid && overlap(cornerRect(c, size), core) === 0) ?? null;
  let tag: Corner | null = clearOf(TAG);
  let tagSize: "full" | "small" | null = tag ? "full" : null;
  if (!tag) {
    tag = clearOf(TAG_SMALL);
    tagSize = tag ? "small" : null;
  }
  let note: Corner | null = null;
  let noteSize: "full" | "small" = "full";
  if (hasNote) {
    const avoid = tag ?? undefined;
    note = clearOf(NOTE, avoid);
    if (!note) {
      noteSize = "small";
      note = clearOf(NOTE_SMALL, avoid) ?? freeCorner(core, NOTE_SMALL, avoid);
    }
  }
  return { mode: fitted ? ("cover" as const) : ("contain" as const), pos: fitted?.pos, inner, subject, core, tag, tagSize, note, noteSize };
}
