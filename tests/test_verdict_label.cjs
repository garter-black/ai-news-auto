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
const { japanLabel, isUnknownValue, verdictFacts } = loaded.exports;

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
  assert.deepEqual(facts.filter((f) => !isUnknownValue(f.value)).map((f) => f.label), ['誰に向いている？']);
});
