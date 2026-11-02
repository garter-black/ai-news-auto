const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// Next.js を起動せず、広告の関連度判定（lib/offers.ts）と「あわせて読みたい」（lib/read-next.ts）を実データで検査する。
const root = path.resolve(__dirname, "..");
process.chdir(root);
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
  mod.require = (name) => {
    if (name === "@/lib/content-fs") return fs;
    if (name === "@/lib/articles") return { getAllArticles: () => [] };
    if (name.startsWith("@/lib/")) return load(path.join(root, "lib", `${name.slice(6)}.ts`));
    if (name.startsWith("./")) return load(path.join(path.dirname(file), `${name}.ts`));
    return original(name);
  };
  mod._compile(compiled, file);
  return mod.exports;
}
const { offerPlanForArticle, offerForArticle, MIN_SCORE } = load(path.join(root, "lib/offers.ts"));
const { readNextForArticle } = load(path.join(root, "lib/read-next.ts"));
const offers = JSON.parse(fs.readFileSync(path.join(root, "content/offers.json"), "utf8"));
const links = JSON.parse(fs.readFileSync(path.join(root, "affiliate_links.json"), "utf8"));

const base = { slug: "t", title: "", seoTitle: "", lede: "", description: "", highlights: [], tags: [], keywords: [], genreSlug: "ai", channelSlug: "", sourceName: "" };
const article = (fields) => ({ ...base, ...fields });
const keys = (offer) => (offer?.items ?? []).map((item) => item.key);

test("関係のない記事（スポーツ・芸能）も広告枠は出す（ジャンルの既定の広告）。関係のないガイドは出さない", () => {
  for (const item of [
    article({ title: "フットサル日本代表、強豪モロッコと対戦", genreSlug: "sports", genre: "スポーツ", tags: ["サッカー"] }),
    article({ title: "任天堂、新作ゲームの体験版を配信", genreSlug: "entertainment", genre: "エンタメ", tags: ["ゲーム"] }),
  ]) {
    const plan = offerPlanForArticle(item);
    assert.equal(plan.offer.fallback, true);
    assert.equal(plan.offer.topic, "fallback-life");
    assert.equal(keys(plan.rail)[0], "abema-premium");
    assert.ok(keys(plan.rail).some((key) => /wifi|wimax|hikari/.test(key)));
    assert.deepEqual(readNextForArticle(item), [], item.title);
  }
});

test("全ジャンルの記事に広告を1つ以上出し、右側と本文中で重ならない", () => {
  const expected = { ai: "fallback-ai", science: "fallback-ai", business: "fallback-work", "tech-gadget": "fallback-gadget", investment: "fallback-money", "real-estate": "fallback-money", public: "fallback-tax", sports: "fallback-life", entertainment: "fallback-life", lifestyle: "fallback-life", other: "fallback-work" };
  for (const [genreSlug, topic] of Object.entries(expected)) {
    const plan = offerPlanForArticle(article({ title: "褒章の受章者が決定", genreSlug }));
    assert.equal(plan.offer.topic, topic, genreSlug);
    assert.ok(plan.rail.items.length >= 1, genreSlug);
    const rail = new Set(keys(plan.rail));
    assert.ok(keys(plan.inline).every((key) => !rail.has(key)), genreSlug);
  }
  // 合う話題がある記事は従来どおり関連の高い広告（既定の広告ではない）
  assert.notEqual(offerPlanForArticle(article({ title: "新NISAの口座数が増加", genreSlug: "business" })).offer.fallback, true);
});

test("ガイドが当たらないお金・税金の記事は、そのジャンルの最新ニュース一覧へ案内する", () => {
  const tax = article({ title: "2027年開始の防衛特別所得税とは", genreSlug: "investment", genre: "投資・マネー" });
  assert.deepEqual(readNextForArticle(tax).map((l) => [l.href, l.kind]), [["/genres/investment", "category"]]);
  const award = article({ title: "秋の褒章、消防関係の受章者が決定", genreSlug: "public", genre: "公共" });
  assert.deepEqual(readNextForArticle(award), []);
});

test("副業・独立の記事にはサーバーや外注の広告を出す", () => {
  const plan = offerPlanForArticle(article({ title: "会社員の副業で個人のホームページを開設する人が増加", genreSlug: "business" }));
  assert.equal(plan.offer.topic, "side-biz");
  assert.ok(keys(plan.rail).includes("xserver"));
});

test("NISA・証券口座の記事（ビジネス）にはネット証券、株式会社という語だけでは出さない", () => {
  const nisa = offerPlanForArticle(article({ title: "楽天証券の総合口座が1,500万を突破", genreSlug: "business" }));
  assert.ok(keys(nisa.rail).some((key) => key === "matsui" || key === "dmm-kabu"));
  // 関連では選ばれず、ジャンルの既定の広告（副業・スキル）になる
  assert.equal(offerForArticle(article({ title: "株式会社〇〇が新工場を建設", genreSlug: "business" }), 12), undefined);
  assert.equal(offerForArticle(article({ title: "円安倒産、52カ月連続で株式会社の倒産が発生", genreSlug: "business", tags: ["倒産"] }), 12), undefined);
  assert.equal(offerPlanForArticle(article({ title: "株式会社〇〇が新工場を建設", genreSlug: "business" })).offer.topic, "fallback-work");
});

test("在宅勤務の記事には回線を出す", () => {
  const plan = offerPlanForArticle(article({ title: "テレワークの通信環境、在宅勤務の会社員の半数が不満", genreSlug: "business" }));
  assert.ok(keys(plan.rail).some((key) => /hikari|wifi|wimax/.test(key)));
});

test("社名の一部（サイバーエージェント）でセキュリティやAIエージェントの広告を出さない", () => {
  const item = article({ title: "ABEMA運営のサイバーエージェント、通期決算を発表へ", genreSlug: "business" });
  assert.equal(offerForArticle(item, 12), undefined);
  assert.ok(!keys(offerPlanForArticle(item).offer).some((key) => ["norton", "virusbuster", "eset", "surfshark"].includes(key)));
});

test("見出しの「ChatGPTのOpenAI」のような会社の説明だけでは話題にしない", () => {
  const offer = offerForArticle(article({ title: "AI半導体のNVIDIAが宇宙観測に協力" }), 12);
  assert.ok(!keys(offer).includes("silicon-power"));
  assert.ok(!keys(offer).includes("mdl"));
});

test("関連の高い順に並び、右側と本文中で同じ商品を出さない", () => {
  const plan = offerPlanForArticle(article({ title: "Claude Codeの新機能でAI駆動開発がさらに速く", tags: ["Claude", "コーディング"], channelSlug: "anthropic" }));
  assert.ok(plan.offer.relevance >= MIN_SCORE);
  const scores = plan.offer.items.map((item) => item.relevance);
  assert.deepEqual(scores, [...scores].sort((a, b) => b - a));
  const rail = new Set(keys(plan.rail));
  assert.ok(keys(plan.inline).every((key) => !rail.has(key)));
});

test("A8の提携リンクとピクセルは登録値のまま使う", () => {
  for (const title of ["副業でブログを始める", "在宅勤務の回線", "新NISAの口座数が増加", "会議の議事録を文字起こし", "フィッシング詐欺が急増", "sports", "entertainment", "public", "tech-gadget"]) {
    const genre = ["sports", "entertainment", "public", "tech-gadget"].includes(title) ? title : "business";
    const offer = offerForArticle(article({ title, genreSlug: genre }), 12) ?? offerForArticle(article({ title }), 12) ?? offerPlanForArticle(article({ title, genreSlug: genre })).offer;
    assert.ok(offer, title);
    for (const item of offer.items.filter((i) => i.kind === "partner")) {
      const partner = offers.partners[item.key];
      const program = links[partner.program];
      assert.equal(item.url, partner.link ?? (partner.page ? `${program.url}&a8ejpredirect=${encodeURIComponent(partner.page)}` : program.url));
      const pixel = partner.pixel ?? program.pixel;
      if (pixel) assert.ok(offer.pixels.includes(pixel) || offer.timedPixels.some((p) => p.url === pixel), item.key);
    }
  }
});

test("あわせて読みたいは同じページに出ているガイドを選ばず、話題に合うものを優先する", () => {
  const item = article({ title: "転職市場でAI人材の求人が増加", genreSlug: "business", genre: "ビジネス" });
  assert.equal(readNextForArticle(item)[0].href, "/choose/ai-career");
  assert.ok(readNextForArticle(item, ["/choose/ai-career"]).every((l) => l.href !== "/choose/ai-career"));
  // 機種のレビューに ChatGPT のガイドは出さない
  assert.deepEqual(readNextForArticle(article({ title: "折りたたみスマホGalaxy Z Fold8の使い心地", genreSlug: "tech-gadget", genre: "ガジェット" })), []);
  const phishing = article({ title: "金融庁、個人情報流出の相談窓口を開設", genreSlug: "public" });
  assert.equal(readNextForArticle(phishing)[0].href, "/compare/security-software");
});

test("税金・ふるさと納税・開業の記事は会計ソフト・ふるさと納税・開業サービスを証券より先に出す", () => {
  const tax = offerPlanForArticle(article({ title: "2027年開始の防衛特別所得税とは", genreSlug: "investment" }));
  assert.equal(tax.offer.topic, "tax-filing");
  assert.ok(["mf-kakutei", "yayoi-kakutei"].includes(keys(tax.rail)[0]));
  assert.equal(offerPlanForArticle(article({ title: "ふるさと納税の新ルール", genreSlug: "public" })).rail.items[0].key, "aupay-furusato");
  const pub = keys(offerPlanForArticle(article({ title: "褒章の受章者が決定", genreSlug: "public" })).offer);
  assert.ok(pub.indexOf("mf-kakutei") < pub.indexOf("matsui"));
  assert.equal(keys(offerPlanForArticle(article({ title: "個人事業主から法人化する手順", genreSlug: "business" })).rail)[0], "yayoi-kaigyou");
  assert.equal(keys(offerPlanForArticle(article({ title: "日銀の展望レポート", genreSlug: "business" })).rail)[0], "yayoi-kaigyou");
});

test("新しいA8案件はリンクと表示計測が同じ素材（a8mat）の組で、PR枠で出す", () => {
  for (const key of ["mf-kakutei", "yayoi-kakutei", "aupay-furusato", "mf-setsuritsu", "yayoi-kaigyou", "yayoi-setsuritsu", "abema-premium"]) {
    const partner = offers.partners[key];
    assert.match(partner.link, /^https:\/\/px\.a8\.net\/svt\/ejp\?a8mat=[0-9A-Z+]+$/);
    assert.equal(partner.pixel.split("a8mat=")[1], partner.link.split("a8mat=")[1], key);
    assert.ok(links[partner.program]?.url, key);
  }
});

test("あわせて読みたい（選び方ガイド）はタグ1つだけでは出さない。Claude の料金の記事に回線のガイドを出さない", () => {
  const { chooseGuideForArticle } = load(path.join(root, "lib/choose.ts"));
  const real = JSON.parse(fs.readFileSync(path.join(root, "content/articles/claude-ultra-plan-price-limits.json"), "utf8"));
  const claude = article({ title: real.title, seoTitle: real.seoTitle, lede: real.lede ?? "", description: real.description ?? "", highlights: real.highlights ?? [], tags: real.tags, keywords: real.keywords ?? [], genreSlug: real.genreSlug });
  assert.ok(claude.tags.includes("料金プラン"));
  assert.equal(chooseGuideForArticle(claude), undefined);
  // 代わりに出る「あわせて読みたい」は Claude の料金ガイド（回線ではない）
  const next = readNextForArticle(claude);
  assert.ok(next.every((link) => link.href !== "/choose/internet-for-ai"));
  if (next.length) assert.match(next[0].href, /claude/);
  // 見出しが回線の話題なら従来どおり出す
  assert.equal(chooseGuideForArticle(article({ title: "楽天モバイルの新料金プラン、データ容量無制限に", tags: ["楽天モバイル"], genreSlug: "tech-gadget" }))?.slug, "internet-for-ai");
  assert.equal(chooseGuideForArticle(article({ title: "転職市場でAI人材の求人が増加", genreSlug: "business" }))?.slug, "ai-career");
});
