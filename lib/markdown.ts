import { marked } from "marked";
import { AFFILIATE_REL, isAffiliateUrl } from "@/lib/affiliate";
import { bareUrlToken } from "@/lib/autolink";

marked.setOptions({ gfm: true, breaks: false });
// 生のURLの自動リンクは、日本語・全角記号・空白の手前で止める（lib/autolink.ts）
marked.use({ tokenizer: { url: (src: string) => bareUrlToken(src) } });

/**
 * 生成AIの出力をそのまま dangerouslySetInnerHTML に流すため、
 * スクリプト実行につながる要素・属性を最小限のホワイトリスト方針で除去する。
 */
function sanitize(html: string): string {
  return html
    .replace(/<\s*(script|style|iframe|object|embed|form)\b[\s\S]*?<\/\s*\1\s*>/gi, "")
    .replace(/<\s*(script|style|iframe|object|embed|form|link|meta)\b[^>]*>/gi, "")
    .replace(/\son[a-z]+\s*=\s*"[^"]*"/gi, "")
    .replace(/\son[a-z]+\s*=\s*'[^']*'/gi, "")
    .replace(/\son[a-z]+\s*=\s*[^\s>]+/gi, "")
    .replace(/(href|src)\s*=\s*"\s*javascript:[^"]*"/gi, '$1="#"')
    .replace(/(href|src)\s*=\s*'\s*javascript:[^']*'/gi, "$1='#'");
}

/**
 * 外部リンクに rel/target を付与。
 * アフィリエイトリンクは rel="sponsored nofollow"（lib/affiliate.ts）。data-aff-pos はクリック計測の位置名
 */
function hardenLinks(html: string, affiliateUrls: string[] = []): string {
  const sponsored = new Set(affiliateUrls);
  return html.replace(
    /<a\s+href="(https?:\/\/[^"]+)"/gi,
    (_match, href: string) =>
      isAffiliateUrl(href.replace(/&amp;/g, "&"), sponsored) || sponsored.has(href)
        ? `<a href="${href}" target="_blank" rel="${AFFILIATE_REL}" data-aff-pos="body"`
        : `<a href="${href}" target="_blank" rel="noopener noreferrer nofollow"`,
  );
}

export async function renderMarkdown(
  markdown: string,
  affiliateUrls: string[] = [],
): Promise<string> {
  // 生成文の「です。 次の文」の半角スペースは、日本語の文中では不自然な隙間に見えるので詰める（2026-11-03 の画面の点検）
  const tidy = (markdown ?? "").replace(/([。！？])[ \u00a0]+(?=[^\s|])/g, "$1");
  const html = await marked.parse(tidy);
  return hardenLinks(sanitize(html), affiliateUrls);
}

/**
 * レンダリング済みHTMLを n 番目の <h2> の直前で二分割する。
 * 記事中盤（第3章の前）に広告を差し込むために使う。
 * 見出しが足りないときは、おおよそ中間の位置でフォールバック分割する。
 */
export function splitBeforeHeading(
  html: string,
  headingIndex: number,
): [string, string] {
  const positions: number[] = [];
  const pattern = /<h2[\s>]/gi;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(html)) !== null) positions.push(match.index);

  const cut = positions[headingIndex - 1];
  if (cut !== undefined) return [html.slice(0, cut), html.slice(cut)];

  // h2 が少ない記事は、後半に一番近い見出し位置で割る
  const fallback = positions[Math.floor(positions.length / 2)];
  if (fallback !== undefined) return [html.slice(0, fallback), html.slice(fallback)];
  return [html, ""];
}

/** 記事本文から目次（h2）を抽出 */
export function extractHeadings(markdown: string): { id: string; text: string }[] {
  const headings: { id: string; text: string }[] = [];
  const lines = (markdown ?? "").split("\n");
  let inCodeBlock = false;

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      inCodeBlock = !inCodeBlock;
      continue;
    }
    if (inCodeBlock) continue;

    const match = /^##\s+(.+?)\s*$/.exec(line);
    if (!match) continue;
    const text = match[1].replace(/[*_`]/g, "").trim();
    headings.push({ id: slugifyHeading(text), text });
  }
  return headings;
}

/** marked のデフォルト heading id 生成に合わせる */
export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/<[!\/a-z].*?>/gi, "")
    .replace(/[ -⁯⸀-⹿\\'!"#$%&()*+,./:;<=>?@[\]^`{|}~]/g, "")
    .replace(/\s/g, "-");
}
