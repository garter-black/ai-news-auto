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
const { japanLabel } = loaded.exports;

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
