// Google Discover の基本（2026-11-04 オーナー承認のピボット・台帳 W32）。
// robots に max-image-preview:large を出す・公式の写真がある記事はシェア画像（og:image）を文字の無い写真にする。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');

const root = path.join(__dirname, '..');
// lib の相対の import（'./thumb-url'）も TypeScript のまま読む
require.extensions['.ts'] = (m, filename) => {
  m._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText, filename);
};
function load(rel) {
  const filename = path.join(root, rel);
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = module.paths;
  m._compile(compiled, filename);
  return m.exports;
}
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');

test('[W32] サイト全体の robots に max-image-preview:large（googlebot だけでなく robots にも）', () => {
  const layout = read('app/layout.tsx');
  const robots = layout.slice(layout.indexOf('robots: {'), layout.indexOf('googleBot:'));
  assert.match(robots, /"max-image-preview": "large"/);
  assert.match(layout, /googleBot: \{[^}]*"max-image-preview": "large"/);
});

test('[W32] 公式の写真がある記事は、写真を og:image の先頭に（文字入りのカバーは2つ目）', () => {
  const { shareImages, hasOfficialPhoto } = load('lib/share-image.ts');
  const photo = 'https://ai-tech-express.jp/img/apple-health-1a2b3c4d.jpg';
  const official = { thumbnail: photo, imageCredit: { kind: 'official' } };
  assert.equal(hasOfficialPhoto(official), true);
  const images = shareImages(official, '/cover/apple-health?v=1', '題名');
  assert.equal(images[0].url, photo);
  assert.equal(images[0].width, 1200);
  assert.equal(images[1].url, '/cover/apple-health?v=1');
});

test('[W32] AI の絵・フリー素材・外部の画像の記事は、これまでどおり見出し入りのカバーが先', () => {
  const { shareImages } = load('lib/share-image.ts');
  const ai = { thumbnail: 'https://ai-tech-express.jp/img/yucho-1.jpg', imageCredit: { kind: 'ai' } };
  assert.equal(shareImages(ai, '/cover/yucho?v=1', '題名')[0].url, '/cover/yucho?v=1');
  const external = { thumbnail: 'https://i.ytimg.com/vi/x/maxresdefault.jpg', imageCredit: { kind: 'official' } };
  assert.equal(shareImages(external, '/cover/x?v=1', '題名')[0].url, '/cover/x?v=1');
  assert.equal(shareImages({ thumbnail: 'https://i.ytimg.com/vi/x/maxresdefault.jpg' }, undefined, '題名')[0].width, 1280);
});

test('[W32] 記事ページの og:image と X のカードは同じ順番の関数を使う', () => {
  const page = read('app/articles/[slug]/page.tsx');
  assert.match(page, /images: shareImages\(article, cover, title\),/);
  assert.match(page, /images: shareImages\(article, cover, title\)\.map/);
});
