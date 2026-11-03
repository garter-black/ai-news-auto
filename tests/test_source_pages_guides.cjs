// 台帳 docs/owner_rules.md の W12（報道機関の発信元ページを「公式発表」と呼ばない・検索に出さない・サイトマップに入れない）と
// W13（ガイド・比較・選び方のページに別の種類の決まり文句を貼らない／「〜しましょう」で締めない）。2026-11-03 の点検 #2・#15。
// 本番の見張りは scripts/owner_rules_audit.py（毎日）。ここは push のたびに落とす。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');

const root = path.join(__dirname, '..');
const cache = new Map();

/** TS を CommonJS にして読む（"@/..." と "./..." の TS の読み込みもたどる） */
function load(rel) {
  const filename = path.join(root, rel);
  if (cache.has(filename)) return cache.get(filename).exports;
  const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, resolveJsonModule: true },
  }).outputText;
  const m = new Module(filename, module);
  m.filename = filename;
  m.paths = module.paths;
  cache.set(filename, m);
  const origRequire = m.require.bind(m);
  m.require = (spec) => {
    let target = null;
    if (spec.startsWith('@/')) target = spec.slice(2);
    else if (spec.startsWith('./') || spec.startsWith('../')) target = path.relative(root, path.join(path.dirname(filename), spec));
    if (target !== null) {
      for (const ext of ['.ts', '.tsx', '']) {
        const p = path.join(root, target + ext);
        if (fs.existsSync(p) && fs.statSync(p).isFile()) return p.endsWith('.json') ? JSON.parse(fs.readFileSync(p, 'utf8')) : load(path.relative(root, p));
      }
    }
    return origRequire(spec);
  };
  m._compile(compiled, filename);
  return m.exports;
}
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');

const OFFICIAL_WORDS = /公式発表|公式ブログ|プレスリリース|公式サイト/;

test('[W12] 話題の見張りの radar-<報道機関> と話題のまとめは報道機関の発信元。radar-official と企業の発信元は違う', () => {
  const { isNewsOutletChannel, isThinSourceChannel } = load('lib/news-outlets.ts');
  for (const s of ['radar-itmedia.co.jp', 'radar-news.mynavi.jp', 'radar-bloomberg.com', 'radar-k-tai.watch.impress.co.jp', 'trends']) {
    assert.equal(isNewsOutletChannel(s), true, s);
    assert.equal(isThinSourceChannel(s), true, s);
  }
  for (const s of ['radar-official', 'openai', 'mhlw', 'apple-newsroom-jp-all', '']) assert.equal(isNewsOutletChannel(s), false, s);
  assert.equal(isThinSourceChannel('radar-official'), true, '各社が混ざった radar-official も検索に出さない');
  assert.equal(isThinSourceChannel('openai'), false);
});

test('[W12] 記事にある radar-<ホスト> は、すべて scripts/fast_lane_rules.json の報道機関の一覧（radar.outlets）にある', () => {
  const outlets = (JSON.parse(read('scripts/fast_lane_rules.json')).radar || {}).outlets || [];
  const hosts = outlets.map((o) => o.host);
  assert.ok(hosts.includes('itmedia.co.jp') && hosts.includes('bloomberg.com'));
  const dir = path.join(root, 'content', 'articles');
  const slugs = new Set();
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.json'))) {
    try {
      const s = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')).channelSlug || '';
      if (s.startsWith('radar-') && s !== 'radar-official') slugs.add(s);
    } catch {}
  }
  for (const s of slugs) {
    const host = s.slice('radar-'.length);
    assert.ok(hosts.some((h) => host === h || host.endsWith(`.${h}`)), `${s} が報道機関の一覧にない（公式ならradar-officialのはず）`);
  }
});

test('[W12] 報道機関の発信元ページの題名・説明に「公式発表」「公式ブログ・プレスリリース」を書かない。社名の後に半角の空白を入れない', () => {
  const { channelDescription, channelTitleTail, channelDisplayLabel } = load('lib/news-outlets.ts');
  for (const [slug, label] of [['radar-itmedia.co.jp', 'ITmedia'], ['radar-bloomberg.com', 'ブルームバーグ'], ['radar-news.mynavi.jp', 'マイナビニュース'], ['trends', '話題のニュース']]) {
    const text = `${label}${channelTitleTail(slug)} ${channelDescription(slug, label, true, 3)}`;
    assert.doesNotMatch(text, OFFICIAL_WORDS, text);
    assert.doesNotMatch(text, new RegExp(`${label} `), '社名の後の半角の空白');
    assert.match(channelDescription(slug, label, true, 3), /報道/);
  }
  // radar-official は SEC の名前で出さない（各社が混ざる）
  assert.doesNotMatch(channelDisplayLabel('radar-official', '米証券取引委員会（SEC）'), /SEC/);
  // 企業の発信元は従来どおり「公式ブログ・プレスリリース」
  assert.match(channelDescription('openai', 'OpenAI公式ブログ', true, 5), /公式ブログ/);
  const view = read('app/channels/[channel]/view.tsx');
  assert.match(view, /isThinSourceChannel\(channel\)/, '報道機関の発信元ページは noindex');
  assert.doesNotMatch(view, /"で公開された公式ブログ・プレスリリースをAIが解析/);
});

test('[W12] 報道機関の発信元はサイトマップと足元の「情報源」に入れない。足元は同じ名前の発信元を1つだけ', () => {
  assert.match(read('lib/sitemap.ts'), /!isThinSourceChannel\(channel\.slug\)/);
  const footer = read('components/Footer.tsx');
  assert.match(footer, /!isThinSourceChannel\(c\.slug\)/);
  assert.match(footer, /seenLabels/);
  // 記事の札・出典の欄も「報道」と書く
  const { sourceBadge } = load('lib/companies.ts');
  assert.equal(sourceBadge({ channelSlug: 'radar-itmedia.co.jp' }), '報道');
  assert.equal(sourceBadge({ channelSlug: 'radar-official' }), '公式発表');
  assert.equal(sourceBadge({ channelSlug: 'mhlw' }), '公式発表');
  assert.match(read('components/SourceRecord.tsx'), /isNewsOutletChannel/);
});

/** データの中の文字列をすべて集める */
function strings(value, out = []) {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => strings(v, out));
  return out;
}

// 種類ごとの決まり文句（その種類のガイドにだけ出てよい言葉）
const CATEGORY_WORDS = [
  { only: ['internet-for-ai'], re: /公称値|規格上の最大値|通信速度|光回線|ホームルーター|WiMAX|ギガ|工事費/ },
  { only: ['ai-career'], re: /求人|転職エージェント|年収|教育訓練給付/ },
  { only: ['dify-hosting'], re: /VPS|セルフホスト|Dify/ },
];

test('[W13] 選び方ガイドに別の種類のガイドの決まり文句を貼らない（転職ガイドに「速度はすべて公称値」が出ていた）', () => {
  const { CHOOSE_GUIDES } = load('lib/choose-data.ts');
  assert.ok(CHOOSE_GUIDES.length >= 3);
  for (const guide of CHOOSE_GUIDES) {
    const text = strings(guide).join('\n');
    for (const { only, re } of CATEGORY_WORDS) {
      if (only.includes(guide.slug)) continue;
      const m = re.exec(text);
      assert.equal(m, null, `${guide.slug} に別の種類の言葉「${m && m[0]}」`);
    }
  }
  // 表の注記の既定は、どのガイドにも当てはまる文だけ（回線の注記は回線のガイドの tableNote に置く）
  const view = read('components/choose/ChooseView.tsx');
  const fallback = /guide\.tableNote \?\? `([^`]*)`/.exec(view);
  assert.ok(fallback, '表の注記の既定が見つからない');
  for (const { re } of CATEGORY_WORDS) assert.doesNotMatch(fallback[1], re);
  const internet = CHOOSE_GUIDES.find((g) => g.slug === 'internet-for-ai');
  assert.match(internet.tableNote || '', /公称値/, '回線のガイドには速度の注記を残す');
});

test('[W13] 比較ガイドにも別の種類の言葉を貼らない（回線・転職の決まり文句）', () => {
  const dir = path.join(root, 'content', 'buying-guides');
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.json'))) {
    const text = strings(JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'))).join('\n');
    assert.doesNotMatch(text, /公称値|規格上の最大値/, `${f} に回線の注記`);
    assert.doesNotMatch(text, /転職エージェント|求人/, `${f} に転職の決まり文句`);
  }
});

// かっこの外で文が「〜ましょう。」で終わるか
function callsOut(text) {
  const { splitSentences } = load('lib/article-sections.ts');
  return text.split('\n').flatMap((line) => splitSentences(line)).filter((s) => /ましょう[。！]?\s*$/.test(s.trim()) && !/[「『（(][^」』）)]*ましょう/.test(s));
}

test('[W13] ガイド・比較・選び方のページの文を「〜しましょう」で締めない（A17 をガイドにも）', () => {
  const { politeText, politeDeep } = load('lib/polite-text.ts');
  assert.equal(politeText('登録前に確認しましょう。'), '登録前に確認するとよいでしょう。');
  assert.equal(politeText('注記を読んでから判断しましょう。'), '注記を読んでから判断するとよいでしょう。');
  assert.equal(politeText('自分で確かめてみましょう。'), '自分で確かめてみるとよいでしょう。');
  assert.equal(politeText('料金は月20ドルです。'), '料金は月20ドルです。');
  // 選び方ガイド（TS のデータ）は元の文を直してある
  const { CHOOSE_GUIDES } = load('lib/choose-data.ts');
  for (const guide of CHOOSE_GUIDES) {
    const bad = callsOut(strings(guide).join('\n'));
    assert.deepEqual(bad, [], `${guide.slug}: ${bad[0]}`);
  }
  // 使い方ガイド・比較ガイド（JSON）は読み込むときに politeDeep を通す。通したあとに「〜しましょう」が残らない
  assert.match(read('lib/howto.ts'), /politeDeep\(/);
  assert.match(read('lib/buying-guides.ts'), /politeDeep\(/);
  for (const dir of ['content/howto', 'content/buying-guides']) {
    for (const f of fs.readdirSync(path.join(root, dir)).filter((x) => x.endsWith('.json'))) {
      const data = politeDeep(JSON.parse(read(`${dir}/${f}`)));
      const bad = callsOut(strings(data).join('\n'));
      assert.deepEqual(bad, [], `${dir}/${f}: ${bad[0]}`);
    }
  }
});

test('[W13] 古い説明（動画を映像・音声ごと解析）と確認用の試作ページを読者に出さない', () => {
  for (const rel of ['app/companies/[company]/view.tsx', 'app/tags/[tag]/view.tsx', 'app/contact/page.tsx', 'app/llms.txt/route.ts']) {
    assert.doesNotMatch(read(rel), /映像・音声ごと解析|動画チャンネルの運営者|公式発表・動画などの要約/, rel);
  }
  const concept = read('app/article-concept/page.tsx');
  assert.match(concept, /x-saisoku-internal/);
  assert.match(concept, /notFound\(\)/);
});
