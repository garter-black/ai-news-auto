const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');

const filename = path.join(__dirname, '../lib/verdict.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, esModuleInterop: true}}).outputText;
const loaded = new Module(filename, module);
loaded.filename = filename;
loaded.paths = module.paths;
loaded._compile(compiled, filename);
const { japanLabel, isUnknownValue, verdictFacts, verdictOf, listUsability } = loaded.exports;

const v = (status, japan) => ({ status, japan, price: '', free: null, audience: '', take: '' });

test('「日本から使える？」: 日本での扱いが分からない提供前の製品を「提供前」と言い切らない（2026-11-03）', () => {
  assert.equal(japanLabel(v('soon', 'unknown')), '日本は未確認');
  assert.equal(japanLabel(v('demo', 'unknown')), '日本は未確認');
  assert.equal(japanLabel(v('soon', 'ok')), '日本でも提供予定');
  assert.equal(japanLabel(v('soon', 'partial')), '条件付きで提供予定');
  assert.equal(japanLabel(v('soon', 'no')), '提供前');
  assert.equal(japanLabel(v('demo', 'no')), '提供前');
  assert.equal(japanLabel(v('limited', 'unknown')), '記載なし');
  assert.equal(japanLabel(v('available', 'ok')), '利用できる');
});

test('「記載なし」「不明」など答えになっていない値は判定カード・一覧に出さない（2026-11-03 の画面の点検）', () => {
  for (const s of ['記載なし', '不明', '参加費の記載なし', '料金は元記事では言及なし', '—', '', '料金は不明', '情報なし'])
    assert.equal(isUnknownValue(s), true, s);
  for (const s of ['無料', '月20ドル', 'セキュリティ研究者', '2026-11-18〜2026-11-28', '不明点は公式ヘルプで確認できる'])
    assert.equal(isUnknownValue(s), false, s);
  // 製品の判定で値が分からない欄は、カード側で省けるように「記載なし」として返る
  const facts = verdictFacts({ status: 'available', japan: 'unknown', price: '参加費の記載なし', free: null, audience: 'セキュリティ研究者', take: '' }, 'product');
  assert.deepEqual(facts.filter((f) => !isUnknownValue(f.value)).map((f) => f.label), ['主な対象']);
  // 「日本は未確認」「未発表」「法人向け」も答えになっていないので出さない。無料枠は「ある」ときだけ（2026-11-03）
  for (const s of ['日本は未確認', '未発表', '法人向け']) assert.equal(isUnknownValue(s), true, s);
  const paid = verdictFacts({ status: 'soon', japan: 'ok', price: '言及なし', free: false, audience: '', take: '' }, 'product');
  assert.equal(paid.filter((f) => !isUnknownValue(f.value)).some((f) => f.label === '無料枠'), false);
});

test('製品・サービスの発表でないニュースには判定も「使える」の札も付けない。スポーツ・制度は札なし（2026-11-03 の画面の点検 A）', () => {
  const verdict = { status: 'available', japan: 'ok', price: '無料', free: true, audience: '', take: '' };
  const leak = { title: '北陸電力の「ほくリンク」会員約55万件のメールアドレスが流出', tags: ['セキュリティ'], genreSlug: 'ai', verdict };
  assert.equal(verdictOf(leak), undefined);
  assert.equal(listUsability(leak), undefined);
  const product = { title: '対話AI「Claude」の最上位プランが「Ultra」に', tags: ['Claude'], genreSlug: 'ai', verdict };
  assert.equal(listUsability(product), 'go');
  const sports = { title: 'なでしこジャパン、W杯予選の日本代表を選出', tags: ['サッカー'], genreSlug: 'sports', verdict: { ...verdict, kind: 'sports' } };
  assert.ok(verdictOf(sports), 'スポーツの判定カードは記事ページに残す');
  assert.equal(listUsability(sports), undefined);
  const pub = { title: '東京都、防災アプリにアンダーパス冠水の警告', tags: ['防災'], genreSlug: 'public', verdict: { ...verdict, kind: 'public' } };
  assert.ok(verdictOf(pub));
  assert.equal(listUsability(pub), undefined);
  assert.equal(verdictOf({ ...pub, title: '東京都の倒産件数、10月は前年比12%増' }), undefined);
  assert.equal(verdictOf({ ...product, type: 'roundup' }), undefined);
});
