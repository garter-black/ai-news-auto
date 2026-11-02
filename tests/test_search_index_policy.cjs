const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');

const filename = path.join(__dirname, '../lib/indexing.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = module.paths;
loaded._compile(compiled, filename);
const { isIndexable, isDistributionEligible, noindexReason, isListingIndexable, canonicalPathOf, newsSitemapReason } = loaded.exports;

const good = { sourceType: 'web', channelSlug: 'official', genreSlug: 'ai', title: 'x', tags: [], bodyLength: 2000, hasJapanValue: true };

test('検索に出す条件（分量・主題・日本向けの情報・重複）', () => {
  assert.equal(noindexReason(good), null);
  assert.equal(noindexReason({ ...good, bodyLength: 1499 }), 'thin');
  assert.equal(noindexReason({ ...good, genreSlug: 'sports', title: '試合結果' }), 'off-focus');
  assert.equal(noindexReason({ ...good, genreSlug: 'public', title: '生成AIの指針' }), null);
  assert.equal(noindexReason({ ...good, genreSlug: 'business', tags: ['AI'] }), null);
  assert.equal(noindexReason({ ...good, hasJapanValue: false }), 'no-japan-value');
  assert.equal(noindexReason({ ...good, hasJapanValue: false, verdict: { japan: 'ok' } }), null);
  assert.equal(noindexReason({ ...good, duplicate: true }), 'duplicate');
  assert.equal(noindexReason({ ...good, sourceType: 'video' }), null);
});

test('手動指定・配信元・まとめ記事の扱いと、既存の配信基準を保つ', () => {
  const short = { ...good, bodyLength: 20 };
  assert.equal(isIndexable({ ...good, indexable: false }), false);
  assert.equal(isIndexable({ ...short, indexable: true }), true);
  assert.equal(isIndexable({ ...good, channelSlug: 'prtimes' }), false);
  assert.equal(isIndexable({ ...short, type: 'roundup' }), true);
  assert.equal(isDistributionEligible(short), false);
  assert.equal(isDistributionEligible(good), true);
  assert.equal(isDistributionEligible({ ...short, type: 'roundup' }), true);
  assert.equal(isDistributionEligible({ ...short, sourceType: 'video' }), true);
  assert.equal(isDistributionEligible({ ...short, channelSlug: 'prtimes' }), false);
});

test('一覧ページは検索対象の記事が3本以上で検索に出す', () => {
  assert.equal(isListingIndexable([good, good]), false);
  assert.equal(isListingIndexable([good, good, { ...good, bodyLength: 1 }]), false);
  assert.equal(isListingIndexable([good, good, good]), true);
});

test('重複記事（duplicateOf）は noindex で canonical は代表へ', () => {
  assert.equal(noindexReason({ ...good, slug: 'b', duplicateOf: 'a' }), 'duplicate');
  assert.equal(noindexReason({ ...good, slug: 'a', duplicateOf: 'a' }), null);
  assert.equal(canonicalPathOf({ slug: 'b', duplicateOf: 'a' }), '/articles/a');
  assert.equal(canonicalPathOf({ slug: 'b' }), '/articles/b');
});

test('ニュース用サイトマップは元の発表から48時間以内・重複でない記事だけ（scripts/indexnow.py news_reason と同じ）', () => {
  const now = Date.parse('2026-11-02T12:00:00Z');
  const fresh = { ...good, slug: 'x', generatedAt: '2026-11-02T06:00:00Z', publishedAt: '2026-11-02T05:00:00Z' };
  assert.equal(newsSitemapReason(fresh, now), null);
  assert.equal(newsSitemapReason({ ...fresh, publishedAt: '2026-10-27T16:00:00Z' }, now), 'stale-source');
  assert.equal(newsSitemapReason({ ...fresh, sourcePublishedAt: '2026-10-29T00:00:00Z' }, now), 'stale-source');
  assert.equal(newsSitemapReason({ ...fresh, generatedAt: '2026-10-30T00:00:00Z' }, now), 'old');
  assert.equal(newsSitemapReason({ ...fresh, duplicateOf: 'y' }, now), 'duplicate');
  assert.equal(newsSitemapReason({ ...fresh, angleOf: 'y' }, now), 'angle');
  assert.equal(newsSitemapReason({ ...fresh, late: true }, now), 'late');
  assert.equal(newsSitemapReason({ ...fresh, type: 'roundup' }, now), 'roundup');
});
