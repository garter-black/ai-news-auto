/**
 * 本文に置く公式の写真・図（記事 JSON の bodyImages。scripts/images.py の body_images が、
 * 会社・団体の自社サイトの発表にある報道向けの写真・図から1〜2枚を選んで /img/ に置く）。
 * 2026-11-03 オーナー決定: 公式の図や写真を、出典つきで本文に1〜2枚入れる。出典（画像：会社名・発表のページ）は必ず出す。
 */

export type BodyImage = {
  src: string;
  width?: number;
  height?: number;
  /** photo（写真）/ kit（報道用素材）/ dense（図・表の画像）/ hero（発表のキービジュアル） */
  kind?: string;
  /** 出典の会社・団体の名前 */
  credit: string;
  /** 画像のある公式発表のページ */
  sourceUrl?: string;
  original?: string;
  alt?: string;
};

const OUR_IMAGE = /^https:\/\/ai-tech-express\.jp\/img\/[\w.-]+\.(?:jpg|jpeg|png|webp)$/;

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** 表示してよい画像だけ（自前の /img/ に置いたもの・出典の名前があるもの）。最大2枚 */
export function usableBodyImages(images: BodyImage[] | undefined): BodyImage[] {
  return (images ?? []).filter((im) => im && OUR_IMAGE.test(im.src ?? "") && (im.credit ?? "").trim()).slice(0, 2);
}

export function bodyImageHtml(im: BodyImage, title: string): string {
  const alt = im.alt?.trim() || `${im.credit}の発表の${im.kind === "dense" ? "図" : "画像"}（${title}）`;
  const size = im.width && im.height ? ` width="${im.width}" height="${im.height}"` : "";
  const source = im.sourceUrl
    ? `（<a href="${esc(im.sourceUrl)}" rel="noopener" target="_blank">公式発表</a>）`
    : "";
  return (
    `<figure class="body-figure not-prose">` +
    `<img src="${esc(im.src)}" alt="${esc(alt)}"${size} loading="lazy" decoding="async" />` +
    `<figcaption class="text-note">画像：${esc(im.credit.trim())}${source}</figcaption>` +
    `</figure>`
  );
}

/** h2 の開始位置の一覧 */
function h2Starts(html: string): number[] {
  const out: number[] = [];
  const re = /<h2[\s>]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(html))) out.push(m.index);
  return out;
}

/** n 番目（0 始まり）の段落の終わり */
function afterParagraph(html: string, n: number): number | null {
  const re = /<\/p>/g;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(html))) {
    if (i === n) return m.index + m[0].length;
    i += 1;
  }
  return null;
}

/**
 * 本文の HTML に画像を差し込む。1枚目は最初の見出しの手前（見出しが無い記事は2つ目の段落の後）、
 * 2枚目は2つ目の見出しの手前（無ければ末尾の手前の段落の後）。
 */
export function insertBodyImages(html: string, images: BodyImage[], title: string): string {
  const list = usableBodyImages(images);
  if (!list.length) return html;
  let out = html;
  list.forEach((im, i) => {
    const piece = bodyImageHtml(im, title);
    const starts = h2Starts(out);
    let at: number | null = starts[i] ?? null;
    if (at === null || at === 0) at = afterParagraph(out, i === 0 ? 1 : 3) ?? afterParagraph(out, 0);
    out = at === null ? out + piece : out.slice(0, at) + piece + out.slice(at);
  });
  return out;
}
