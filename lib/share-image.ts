/**
 * 記事のシェア画像（og:image・X のカード）の順番（2026-11-04 オーナー承認のピボット・台帳 W32）。
 *
 * Google Discover（スマホの Google アプリのおすすめ欄）は、幅 1,200px 以上の大きな写真と
 * robots の max-image-preview:large がある記事を大きなカードで出す。Google は「ロゴや文字の多い画像は避ける」と案内している。
 * これまでは公式の写真がある記事でも、見出しの文字入りのカバー（/cover。1200×630）を先に出していた。
 *
 * - 公式発表の写真（scripts/images.py が縮小してサイトで配信する /img/<名前>.jpg。幅 1,200px）がある記事は、その写真を先に出す
 * - 公式の写真が無い記事（AI の絵・フリー素材・外部の画像）は、これまでどおり見出し入りのカバーが先
 * - カバーは2つ目に残す（題名は1つだけ。カバーの題名も記事の題名から作る＝台帳 A29）
 */
import { isOwnImage } from "./thumb-url";

type ShareImageInput = {
  thumbnail?: string;
  imageCredit?: { kind?: string } | null;
};

export type ShareImage = { url: string; width?: number; height?: number; alt?: string };

/** 公式発表の写真をシェア画像の先頭にしてよいか（サイトで配信する幅 1,200px の写真で、出典が公式発表） */
export function hasOfficialPhoto(article: ShareImageInput): boolean {
  return isOwnImage(article.thumbnail) && article.imageCredit?.kind === "official";
}

/** og:image の並び。cover は見出し入りのカバーの URL（無ければ空） */
export function shareImages(article: ShareImageInput, cover: string | undefined, alt: string): ShareImage[] {
  const card: ShareImage[] = cover ? [{ url: cover, width: 1200, height: 630, alt }] : [];
  if (hasOfficialPhoto(article)) return [{ url: article.thumbnail as string, width: 1200, alt }, ...card];
  if (card.length) return card;
  return article.thumbnail ? [{ url: article.thumbnail, width: 1280, height: 720 }] : [];
}
