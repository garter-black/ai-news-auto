// 記事のサムネイルを読者のブラウザに直接読ませてよいか（lib/article-index.mjs の servableThumb）と、
// 自前の写真の srcset が src と同じホストを指すか（lib/thumb-url.ts の ownSrcSet。2026-11-03 の壊れた画像の原因）
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

test("外部サイトの画像は直リンクしない（自前と YouTube だけ通す）", async () => {
  const { servableThumb, withDefaults } = await import("../lib/article-index.mjs");
  assert.equal(servableThumb("https://ai-tech-express.jp/img/a-1234abcd.jpg"), "https://ai-tech-express.jp/img/a-1234abcd.jpg");
  assert.equal(servableThumb("/cover/a"), "/cover/a");
  assert.equal(servableThumb("https://i.ytimg.com/vi/x/maxresdefault.jpg"), "https://i.ytimg.com/vi/x/maxresdefault.jpg");
  assert.equal(servableThumb("https://news.mynavi.jp/article/1/index_images/index.jpg"), "");
  assert.equal(servableThumb("https://img.global.news.samsung.com/x.jpg"), "");
  assert.equal(servableThumb(undefined), "");
  assert.equal(withDefaults({ thumbnail: "https://www.mlit.go.jp/common/1.png" }).thumbnail, "");
});

test("自前の写真の srcset は相対パスにしない", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "lib", "thumb-url.ts"), "utf8");
  const body = src.slice(src.indexOf("export function ownSrcSet"), src.indexOf("export function isOwnImage"));
  assert.ok(!/replace\(/.test(body), "ownSrcSet がホストを取り除いている");
});
