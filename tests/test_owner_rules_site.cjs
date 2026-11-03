// オーナーのルール（docs/owner_rules.md）のうち、サイトのコードだけで確かめられるもの。
// 本番の見張りは scripts/owner_rules_audit.py（毎日）。ここは push のたびに落とす。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');

const root = path.join(__dirname, '..');

function load(rel) {
  const filename = path.join(root, rel);
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, esModuleInterop: true } }).outputText;
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = module.paths;
  m._compile(compiled, filename);
  return m.exports;
}

function files(dirs) {
  const out = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name);
      if (fs.statSync(p).isDirectory()) { if (name !== 'node_modules') walk(p); continue; }
      if (/\.(tsx?|mjs|css)$/.test(name)) out.push(path.relative(root, p).split(path.sep).join('/'));
    }
  };
  for (const d of dirs) walk(path.join(root, d));
  return out;
}
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const SITE = files(["app", "components", "lib", "cloudflare"]).filter((rel) => !/\.test\.(ts|tsx|mjs)$/.test(rel));

test('[A9] 画像の赤い「速報」は本当の速報だけ（breaking か公式発表から60分以内）。まとめ記事には付けない', () => {
  const { isFastNews } = load('lib/cover-path.ts');
  assert.equal(isFastNews({ breaking: true }), true);
  assert.equal(isFastNews({ speedMinutes: 12 }), true);
  assert.equal(isFastNews({ speedMinutes: 0 }), true);
  assert.equal(isFastNews({}), false);
  assert.equal(isFastNews({ breaking: false, speedMinutes: null }), false);
  assert.equal(isFastNews({ breaking: true, type: 'roundup' }), false);
});

test('[W5] 連絡先は contact@ai-tech-express.jp（ほかのメールアドレスをサイトに書かない）', () => {
  assert.match(read('site.config.ts'), /email:\s*"contact@ai-tech-express\.jp"/);
  const bad = [];
  for (const rel of SITE) {
    for (const m of read(rel).matchAll(/[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z.]{2,}/g)) {
      const e = m[0].toLowerCase();
      if (e === 'contact@ai-tech-express.jp' || /@(example\.(com|jp)|ai-tech-express\.jp|types\/|next\/|vercel\/|cloudflare\/|opennextjs\/)/.test(e)) continue;
      if (/\.(png|jpg|svg|webp|js|ts|tsx|css)$/.test(e) || /^[a-z-]+@\d/.test(e)) continue; // パッケージ名@版 など
      bad.push(`${rel}: ${m[0]}`);
    }
  }
  assert.deepEqual(bad, []);
});

test('[W6] オーナーの名前は /about と /editorial-policy だけ（全ページの構造化データにも入れない）', () => {
  const allowed = new Set(['app/about/page.tsx', 'app/editorial-policy/page.tsx']);
  const bad = SITE.filter((rel) => !allowed.has(rel) && (/operator\.representative|小室/.test(read(rel))));
  assert.deepEqual(bad, []);
});

test('[A12] 人の確認について実態と違う約束を書かない（全記事を人が確認、確認済み＝人が読んだ記事の一覧）', () => {
  const claim = /(?:全て|すべて|全部)の記事を(?:人|編集部|編集者|スタッフ)が(?:確認|チェック|読ん|目を通)(?:し|で)て?います|全記事を(?:人|編集部|編集者|スタッフ)が(?:確認|チェック)|(?:人|編集者|編集部)が(?:1本ずつ|一本ずつ)(?:確認|チェック)/;
  const bad = SITE.filter((rel) => claim.test(read(rel)));
  assert.deepEqual(bad, []);
});

test('[W8] 嫌いな見た目を使わない: Zen Kaku の書体・AIっぽい「↗」の矢印', () => {
  const bad = [];
  for (const rel of SITE) {
    const src = read(rel).split('\n').filter((line) => !/^\s*(\/\/|\*|\/\*)/.test(line)).join('\n'); // 説明の行は除く
    if (/Zen[_ ]Kaku/.test(src)) bad.push(`${rel}: Zen Kaku`);
    if (/↗/.test(src)) bad.push(`${rel}: ↗`);
  }
  assert.deepEqual(bad, []);
});

test('[W2] ホームのストーリーの丸はコンパクト（スマホは 60〜70px の丸を詰めて 4〜5 個見せる。PC でも 80px まで）', () => {
  const css = read('components/story/SwipeDeck.tsx');
  const px = (re) => { const m = css.match(re); assert.ok(m, `見つからない: ${re}`); return Number(m[1]); };
  const img = px(/\.sr-img\{[^}]*?width:(\d+)px/);
  assert.ok(img >= 60 && img <= 70, `スマホの丸 ${img}px`);
  const ring = px(/\.sr-ring\{[^}]*?width:(\d+)px/);
  assert.ok(ring <= 72, `スマホの丸の幅 ${ring}px`);
  for (const m of css.matchAll(/\.sr-img\{[^}]*?width:(\d+)px/g)) assert.ok(Number(m[1]) <= 80, `丸が大きすぎる ${m[1]}px`);
  assert.match(css, /\.sr-label\{[^}]*white-space:nowrap/);
});
