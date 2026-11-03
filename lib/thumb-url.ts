/**
 * サムネイルの URL を枠の大きさに合わせて軽い版に替える（クライアント部品からも使う）。
 *
 * - サイトで配信する写真（/img/<名前>.jpg。scripts/images.py が Workers KV に置く）は一覧用の 640px WebP
 * - YouTube は高解像度版（maxresdefault）を標準画質（hqdefault・mqdefault）に
 */
const OWN_IMAGE = /^((?:https:\/\/[^/]+)?\/img\/[A-Za-z0-9][A-Za-z0-9_-]*)\.jpg$/;

export function smallThumb(url: string, youtube: "hqdefault" | "mqdefault" = "hqdefault"): string {
  const own = OWN_IMAGE.exec(url);
  if (own) return `${own[1]}.sm.webp`;
  return url.replace("maxresdefault", youtube);
}

/**
 * サイトの写真（/img/<名前>.jpg。scripts/images.py が Workers KV に置く）を枠の幅で選ばせる候補（srcset）。
 * 幅 400 / 640 / 800 / 1200 の WebP を KV に用意してある（無い古い写真は worker が元の JPEG を返す）。
 * スマホは 400〜800px（10〜30KB）で足り、PC の大きな枠だけ 1200px を読む（LCP 対策）
 *
 * 候補は src と同じホスト（https://ai-tech-express.jp/img/…）のまま出す。
 * 以前は "/img/…" の相対パスにしていたため、本番の worker 以外（手元の next dev・プレビュー・翻訳プロキシなど）で開くと
 * srcset だけが存在しない /img/ を指し、src は正しいのに壊れた画像アイコンになっていた（2026-11-03）
 */
export function ownSrcSet(url: string): string | null {
  const own = OWN_IMAGE.exec(url);
  if (!own) return null;
  const path = own[1];
  return `${path}.w400.webp 400w, ${path}.sm.webp 640w, ${path}.w800.webp 800w, ${path}.w1200.webp 1200w`;
}

/** サイトの写真（/img/<名前>.jpg、幅 1200px までの JPEG）か */
export function isOwnImage(url: string | undefined): boolean {
  return Boolean(url && OWN_IMAGE.test(url));
}

const YT_MAXRES_WEBP = /^https:\/\/i\.ytimg\.com\/vi_webp\/([^/]+)\/maxresdefault\.webp$/;

/** YouTube の高解像度サムネイル（1280px・約90KB）。スマホは 640px 版（sddefault・約35KB）で足りる */
export function ytSrcSet(url: string): string | null {
  const m = YT_MAXRES_WEBP.exec(url);
  return m ? `https://i.ytimg.com/vi_webp/${m[1]}/sddefault.webp 640w, ${url} 1280w` : null;
}

/** 軽い版が読めなかったときの元の画像（替えていなければ null） */
export function largeThumb(url: string): string | null {
  return /\/img\/[A-Za-z0-9][A-Za-z0-9_-]*\.sm\.webp$/.test(url) ? url.replace(/\.sm\.webp$/, ".jpg") : null;
}
