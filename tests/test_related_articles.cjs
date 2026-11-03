const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 記事ページの「関連記事」「次に読むならこれ」の選び方（lib/related.ts。台帳 W19・2026-11-03 の点検の6番）を Next.js を起動せずに確かめる。
const root = path.resolve(__dirname, "..");
const cache = new Map();
function load(file) {
  if (cache.has(file)) return cache.get(file).exports;
  const compiled = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  const mod = new Module(file, module);
  mod.filename = file;
  mod.paths = Module._nodeModulePaths(path.dirname(file));
  cache.set(file, mod);
  const original = mod.require.bind(mod);
  mod.require = (name) => (name.startsWith("@/lib/") ? load(path.join(root, "lib", `${name.slice(6)}.ts`)) : original(name));
  mod._compile(compiled, file);
  return mod.exports;
}
const { relatedArticles, nextReads, similarity, isDisasterArticle } = load(path.join(root, "lib/related.ts"));
const { companyOf } = load(path.join(root, "lib/companies.ts"));
const company = (a) => companyOf(a).slug;

const base = { title: "", tags: [], keywords: [], genreSlug: "business", channelSlug: "", sourceUrl: "", publishedAt: "2026-11-02T00:00:00Z" };
let n = 0;
const art = (fields) => ({ ...base, slug: `a${n++}`, ...fields });

// 監査で見つかった組み合わせ（Appleの決算・年金月間・フットサル・リフォーム会社）と、同じジャンルの無関係な記事
const apple = art({ slug: "apple-q4", title: "Apple、7〜9月期の売上高が12％増の1145億ドル", genreSlug: "tech-gadget", tags: ["Apple", "決算", "スマートフォン", "業績", "ビジネス"], keywords: ["Apple", "決算", "iPhone Duo", "配当"] });
const appleDuo = art({ slug: "apple-duo", title: "Apple、折りたたみ型「iPhone Duo」向け最適化アプリを発表", genreSlug: "tech-gadget", tags: ["スマートフォン", "Apple", "ガジェット"], keywords: ["iPhone Duo", "Apple", "Netflix"] });
const appleMac = art({ slug: "apple-mac", title: "Appleが「M6」搭載の14インチMacBook Proを発表", genreSlug: "tech-gadget", tags: ["PC", "家電"], keywords: ["MacBook Pro", "M6", "Apple"], publishedAt: "2026-10-20T00:00:00Z" });
const samsung = art({ slug: "samsung-yog", title: "サムスン、ユース五輪ダカール大会に「Galaxy S26 Ultra」を提供", genreSlug: "tech-gadget", tags: ["スマートフォン", "通信", "家電"], keywords: ["サムスン", "Galaxy S26 Ultra"] });
const ipa = art({ slug: "ipa-dx", title: "IPA、DX認定制度で118件を公表", genreSlug: "tech-gadget", tags: ["ビジネス", "セキュリティ", "アプリ", "通信"], keywords: ["IPA", "DX認定"] });
const nenkin = art({ slug: "nenkin", title: "厚労省、11月30日の「年金の日」と11月の「ねんきん月間」で啓発", genreSlug: "public", tags: ["年金", "社会・行政", "ライフプラン"], keywords: ["厚生労働省", "日本年金機構", "ねんきんネット"] });
const izoku = art({ slug: "izoku", title: "厚労省、11月1日から「遺族年金受給者実態調査」を実施", genreSlug: "public", tags: ["年金", "社会保障", "行政"], keywords: ["厚生労働省", "遺族年金", "日本年金機構"] });
const quake = art({ slug: "quake-20261029000759", title: "【地震】千葉県北東部で最大震度5弱、M5.4（気象庁）", genreSlug: "public", channelSlug: "jma", tags: ["地震", "防災"], keywords: ["地震", "千葉県"] });
const tokyo = art({ slug: "tokyo-challenge", title: "東京都、地域企業グループチャレンジ促進事業で4グループを決定", genreSlug: "public", tags: ["社会・行政", "中小企業支援", "東京都"], keywords: ["東京都"] });
const futsal = art({ slug: "futsal", title: "フットサル日本代表、11月6日・8日に大分でモロッコ代表と親善試合", genreSlug: "sports", tags: ["サッカー", "フットサル", "日本代表", "モロッコ"], keywords: ["フットサル日本代表"] });
const bounty = art({ slug: "openai-bounty", title: "OpenAI、セキュリティのバグ報奨金を増額", genreSlug: "ai", tags: ["OpenAI", "サイバーセキュリティ"], keywords: ["OpenAI", "バグバウンティ"] });
const reform = art({ slug: "reform", title: "リフォームテクノロジー、持株会社体制へ移行", genreSlug: "business", tags: ["住宅設備", "DX", "企業再編"], keywords: ["交換できるくん", "持株会社"] });
const beer = art({ slug: "beer", title: "地ビール1-8月出荷量は2年連続増", genreSlug: "business", tags: ["ビジネス", "統計", "小売", "飲食"], keywords: ["地ビール", "東京商工リサーチ"] });
const tobacco = art({ slug: "tobacco", title: "財務省、製造たばこの小売定価の認可情報を随時公開　日付ごとにPDFで確認可能", genreSlug: "business", tags: ["ビジネス", "統計"], keywords: ["PDF", "たばこ"] });
const gemini = art({ slug: "gemini-pdf", title: "Gemini、ファイルを直接編集", genreSlug: "ai", tags: ["Gemini"], keywords: ["PDF", "Google"] });
const dup = art({ slug: "apple-q4-dup", title: "Apple、第4四半期の業績を発表", genreSlug: "tech-gadget", tags: ["Apple", "決算"], keywords: ["Apple", "決算"], duplicateOf: "apple-q4" });
// 文書頻度の母数（多くの記事に付く語を「話題」と数えない）を実データに近づけるための、関係のない記事
const filler = Array.from({ length: 60 }, (_, i) => art({ title: `新サービス${i}を開始`, tags: ["ビジネス", "統計", "社会・行政", "スマートフォン"], keywords: [`語${i}`] }));
const all = [apple, appleDuo, appleMac, samsung, ipa, nenkin, izoku, quake, tokyo, futsal, bounty, reform, beer, tobacco, gemini, dup, ...filler];
const now = Date.parse("2026-11-03T00:00:00Z");
const slugs = (items) => items.map((a) => a.slug);

test("Appleの決算の関連記事は Apple の記事だけ（同じジャンルのサムスン・IPA は出さない。重複記事も出さない）", () => {
  const got = slugs(relatedArticles(apple, all, company, 4, now));
  assert.deepEqual(got.sort(), ["apple-duo", "apple-mac"]);
  assert.ok(!got.includes("apple-q4-dup"));
});

test("年金の記事の関連に地震・同じ分野の別の話題を出さない（同じ年金の記事は出す）", () => {
  const got = slugs(relatedArticles(nenkin, all, company, 4, now));
  assert.deepEqual(got, ["izoku"]);
  const next = nextReads(nenkin, all, company, 3, now);
  assert.ok(next.every((x) => x.article.slug !== "tokyo-challenge" && x.article.slug !== "quake-20261029000759"));
  // 地震の記事の関連にも、ほかの記事を混ぜない
  assert.deepEqual(slugs(relatedArticles(quake, all, company, 4, now)), []);
  assert.ok(isDisasterArticle(quake) && !isDisasterArticle(nenkin));
});

test("関係のある記事が無ければ空（フットサル・リフォーム・たばこに無関係な記事で埋めない）", () => {
  assert.deepEqual(slugs(relatedArticles(futsal, all, company, 4, now)), []);
  assert.deepEqual(slugs(relatedArticles(reform, all, company, 4, now)), []);
  // 「PDF」のような分野の語1つだけでは関連にしない
  assert.deepEqual(slugs(relatedArticles(tobacco, all, company, 4, now)), []);
  assert.equal(similarity(tobacco, gemini, all, company).strong, false);
});

test("記事を作るときに使った過去記事（relatedSlugs）も、話題が重ならなければ出さない", () => {
  const withCtx = { ...nenkin, relatedSlugs: ["quake-20261029000759", "tokyo-challenge", "izoku"] };
  assert.deepEqual(slugs(relatedArticles(withCtx, all, company, 4, now)), ["izoku"]);
});

test("「同じ話題の記事」の札は語が2つ以上重なる記事だけ。名前（企業）だけ重なる記事は「同じ企業の発表」", () => {
  const next = nextReads(apple, all, company, 3, now);
  const reason = Object.fromEntries(next.map((x) => [x.article.slug, x.reason]));
  assert.equal(reason["apple-duo"], "同じ話題の記事");
  assert.equal(reason["apple-mac"], "同じ企業の発表");
  assert.ok(!("samsung-yog" in reason) && !("ipa-dx" in reason));
});

test("実データ: 関連記事は話題の基準を満たすものだけ・地震と混ぜない・自分と重複を出さない", () => {
  const dir = path.join(root, "content/articles");
  const real = fs
    .readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => ({ tags: [], keywords: [], ...JSON.parse(fs.readFileSync(path.join(dir, f), "utf8")) }));
  for (const a of real) {
    const got = relatedArticles(a, real, company, 4, now);
    for (const b of got) {
      assert.notEqual(b.slug, a.slug);
      assert.equal(isDisasterArticle(b), isDisasterArticle(a), `${a.slug} → ${b.slug}`);
      assert.ok(similarity(a, b, real, company).strong, `${a.slug} → ${b.slug}`);
      assert.notEqual(b.duplicateOf, a.slug, `${a.slug} → ${b.slug}`);
    }
  }
});

test("記事ページは lib/related.ts の選び方を使う（同じジャンルに点数を足す古い選び方に戻さない）", () => {
  const src = fs.readFileSync(path.join(root, "lib/articles.ts"), "utf8");
  assert.match(src, /relatedArticles\(article, getAllArticles\(\)/);
  assert.match(src, /nextReads\(article, getAllArticles\(\)/);
  assert.ok(!/genreSlug === article\.genreSlug \? 5/.test(src));
});
