import type { Tokens } from "marked";

/**
 * 本文の「生のURL」を自動リンクにするときの終わりの見分け方（2026-11-03 の不具合の修正）。
 *
 * marked の GFM 自動リンクは空白か < までをURLとみなすため、日本語の文に直接続くURL
 * （例:「URLはhttps://example.com/a/bである。」）だと「である。」までリンクに入ってしまう。
 * 実際のURLに日本語や全角の記号がそのまま入ることはまずないので、
 * - ASCII 以外の文字（日本語・全角の 。、）」 など）と空白で必ず止める
 * - 末尾の . , : ; ! ? ' " * _ ~ と、対になっていない ) ] は外す
 */

const URL_START = /^(?:https?:\/\/|ftp:\/\/|www\.)(?=[A-Za-z0-9])/i;
// RFC 3986 でURLに使える ASCII の文字だけ
const URL_CHARS = /^[A-Za-z0-9\-._~:/?#[\]@!$&'()*+,;=%]+/;
const TRAILING = /[.,:;!?'"*_~]$/;

function count(text: string, ch: string): number {
  let n = 0;
  for (const c of text) if (c === ch) n += 1;
  return n;
}

/** src の先頭が生のURLなら、そのURL部分を返す（なければ null） */
export function matchBareUrl(src: string): string | null {
  if (!URL_START.test(src)) return null;
  let url = URL_CHARS.exec(src)?.[0] ?? "";
  for (;;) {
    const before = url;
    if (TRAILING.test(url)) url = url.slice(0, -1);
    else if (url.endsWith(")") && count(url, ")") > count(url, "(")) url = url.slice(0, -1);
    else if (url.endsWith("]") && count(url, "]") > count(url, "[")) url = url.slice(0, -1);
    if (url === before) break;
  }
  const start = URL_START.exec(url)?.[0] ?? "";
  // スキームだけ（"https://" の直後が記号）なら URL とみなさない
  return start && url.length > start.length ? url : null;
}

/** marked の url トークナイザの差し替え。メールアドレスなどは marked 標準に任せる（false） */
export function bareUrlToken(src: string): Tokens.Link | false {
  const url = matchBareUrl(src);
  if (!url) return false;
  const href = /^www\./i.test(url) ? `http://${url}` : url;
  return { type: "link", raw: url, text: url, href, tokens: [{ type: "text", raw: url, text: url }] };
}
