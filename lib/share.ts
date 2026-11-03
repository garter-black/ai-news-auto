/**
 * 記事のシェアボタンの行き先（各 SNS の公式の共有 URL だけ。外部のスクリプトは読まない）。
 * どこから来たかを数えられるよう、記事の URL に utm_source=share_<SNS>&utm_medium=social を付ける。
 * cloudflare/worker.ts はエッジのキャッシュのキーから utm_* を外すので、付けても表示は遅くならない。
 */
export type SharePlatform = "x" | "threads" | "facebook" | "line" | "bluesky";

export const SHARE_PLATFORMS: { id: SharePlatform; label: string }[] = [
  { id: "x", label: "X" },
  { id: "threads", label: "Threads" },
  { id: "facebook", label: "Facebook" },
  { id: "line", label: "LINE" },
  { id: "bluesky", label: "Bluesky" },
];

/** utm を付けた記事の URL（source は share_x / share_copy / share_native など） */
export function sharedUrl(url: string, source: string): string {
  const u = new URL(url);
  u.searchParams.set("utm_source", source);
  u.searchParams.set("utm_medium", "social");
  return u.toString();
}

/** 各 SNS の投稿画面の URL */
export function shareHref(platform: SharePlatform, url: string, title: string): string {
  const link = sharedUrl(url, `share_${platform}`);
  const e = encodeURIComponent;
  switch (platform) {
    case "x":
      return `https://x.com/intent/post?text=${e(title)}&url=${e(link)}`;
    case "threads":
      return `https://www.threads.com/intent/post?text=${e(`${title}\n${link}`)}`;
    case "facebook":
      return `https://www.facebook.com/sharer/sharer.php?u=${e(link)}`;
    case "line":
      return `https://social-plugins.line.me/lineit/share?url=${e(link)}`;
    case "bluesky":
      return `https://bsky.app/intent/compose?text=${e(`${title}\n${link}`)}`;
  }
}
