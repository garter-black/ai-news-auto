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
  const sports = { title: 'なでしこジャパン、W杯予選の日本代表を選出', tags: ['サッカー'], genreSlug: 'sports', verdict: { ...verdict, kind: 'sports', when: '11月28日 19:00' } };
  assert.ok(verdictOf(sports), 'スポーツの判定カードは記事ページに残す（日程が分かる）');
  assert.equal(listUsability(sports), undefined);
  const pub = { title: '東京都、防災アプリにアンダーパス冠水の警告', tags: ['防災'], genreSlug: 'public', verdict: { ...verdict, kind: 'public' } };
  assert.ok(verdictOf(pub));
  assert.equal(listUsability(pub), undefined);
  assert.equal(verdictOf({ ...pub, title: '東京都の倒産件数、10月は前年比12%増' }), undefined);
  assert.equal(verdictOf({ ...product, type: 'roundup' }), undefined);
});

test('札は記事の中身と合うものだけ: お知らせ・統計の転載、制度の説明、答えになっていない観戦の札は出さない（2026-11-03 オーナー決定・台帳 A27）', () => {
  const available = { status: 'available', japan: 'ok', price: '無料', free: true, audience: '国民全般', take: '' };
  // 年金月間（毎年のお知らせ）に「受付中・実施中／申し込みの可否」が付いていた
  const nenkin = {
    title: '厚労省、11月30日の「年金の日」と11月の「ねんきん月間」で啓発　「ねんきんネット」の活用を呼びかけ',
    tags: ['年金', '社会・行政'], genreSlug: 'public',
    verdict: { ...available, kind: 'public', take: 'ねんきんネットで自身の年金記録や将来の受給見込額を確認し、高齢期の生活設計に備える。' },
  };
  assert.equal(verdictOf(nenkin), undefined);
  // 決算発表日の予告・統計の転載・褒章の一覧
  for (const title of ['サイバーエージェント、2026年9月期の通期決算を11月11日15時半に発表へ', '地ビール1-8月出荷量は2年連続増　首位はエチゴビール', '国土交通省、令和8年秋の褒章受章者を発表'])
    assert.equal(verdictOf({ title, tags: [], genreSlug: 'business', verdict: available }), undefined, title);
  // 申し込める制度・使えるアプリには残す
  assert.ok(verdictOf({ title: '東京都、特別高圧電力・工業用LPガスの支援金で第7回申請受付　最大750万円', tags: [], genreSlug: 'public', verdict: { ...available, kind: 'public' } }));
  assert.ok(verdictOf({ title: '政府広報オンラインが「国の教育ローン」を解説　融資は1人350万円以内', tags: [], genreSlug: 'public', verdict: { ...available, kind: 'public' } }));
  // 制度の記事でも、手続き・窓口・アプリの無い発表には出さない
  assert.equal(verdictOf({ title: '内閣府、新しい会議体の設置を決定', tags: [], genreSlug: 'public', verdict: { ...available, kind: 'public', take: '' } }), undefined);
  // バグ報奨金（制度）に「一部で提供中」
  const bounty = { title: 'OpenAI、セキュリティのバグ報奨金を増額　重大な欠陥は最大3万ドル', tags: ['OpenAI', 'セキュリティ'], genreSlug: 'ai', verdict: { ...available, status: 'limited' } };
  assert.equal(verdictOf(bounty), undefined);
  assert.equal(listUsability(bounty), undefined);
  // 方針の発表（「使えるサービスではなく方針の発表」）
  const policy = { title: 'OpenAI、電力対策「Grid Protectors」を発表', tags: ['OpenAI', '生成AI'], genreSlug: 'ai', verdict: { ...available, status: 'soon', take: '使えるサービスではなく方針の発表。各地の合意内容が公表されてから判断すれば十分です。' } };
  assert.equal(verdictOf(policy), undefined);
  // フットサルの親善試合: ひとことが観戦の方法に答えておらず、日程・放送も無い
  const futsal = { title: 'フットサル日本代表、11月6日・8日に大分でモロッコ代表と親善試合', tags: ['フットサル'], genreSlug: 'sports',
    verdict: { ...available, kind: 'sports', price: '言及なし', take: '世界ランキング6位の強豪モロッコとの年内最後の大一番となる。' } };
  assert.equal(verdictOf(futsal), undefined);
  assert.ok(verdictOf({ ...futsal, verdict: { ...futsal.verdict, take: 'チケットは11月1日から販売。DAZNで生配信されます。' } }));
});

test('判定カードは、ニュースの種類が「新製品・料金・制度」の記事だけ（scripts/news_types.py の verdict=True と同じ。2026-11-03）', () => {
  const src = fs.readFileSync(path.join(__dirname, '../scripts/news_types.py'), 'utf8');
  // news_types.py の NewsType(... key="x" ... verdict=True ...) を拾う
  const allowed = new Set();
  for (const block of src.split(/\n(?=_add\(NewsType\()/)) {
    const key = /key="([a-z-]+)"/.exec(block);
    if (key && /\bverdict=True\b/.test(block)) allowed.add(key[1]);
  }
  const { VERDICT_NEWS_TYPES } = loaded.exports;
  assert.deepEqual([...VERDICT_NEWS_TYPES].sort(), [...allowed].sort());
  const verdict = { status: 'available', japan: 'ok', price: '無料', free: true, audience: '', take: '' };
  const base = { title: 'Google、Classroomに学習モード', tags: ['Google'], genreSlug: 'ai', verdict };
  assert.ok(verdictOf({ ...base, newsType: 'product' }));
  assert.equal(verdictOf({ ...base, newsType: 'earnings' }), undefined);
  assert.equal(verdictOf({ ...base, newsType: 'security' }), undefined);
  assert.ok(verdictOf(base), '種類の無い古い記事は従来どおり');
});

