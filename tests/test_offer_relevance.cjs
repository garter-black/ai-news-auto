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

test("関係のない記事（スポーツ・芸能）には広告を出さず、自前のガイドを案内する", () => {
  for (const item of [
    article({ title: "フットサル日本代表、強豪モロッコと対戦", genreSlug: "sports", tags: ["サッカー"] }),
    article({ title: "任天堂、新作ゲームの体験版を配信", genreSlug: "entertainment", tags: ["ゲーム"] }),
  ]) {
    assert.deepEqual(offerPlanForArticle(item), {});
    const [next] = readNextForArticle(item);
    assert.ok(next, item.title);
    assert.match(next.href, /^\/(guides|choose|compare)\//);
    assert.ok(next.label.length > 0);
  }
});

test("副業・独立の記事にはサーバーや外注の広告を出す", () => {
  const plan = offerPlanForArticle(article({ title: "会社員の副業で個人のホームページを開設する人が増加", genreSlug: "business" }));
  assert.equal(plan.offer.topic, "side-biz");
  assert.ok(keys(plan.rail).includes("xserver"));
});

test("NISA・証券口座の記事（ビジネス）にはネット証券、株式会社という語だけでは出さない", () => {
  const nisa = offerPlanForArticle(article({ title: "楽天証券の総合口座が1,500万を突破", genreSlug: "business" }));
  assert.ok(keys(nisa.rail).some((key) => key === "matsui" || key === "dmm-kabu"));
  assert.deepEqual(offerPlanForArticle(article({ title: "株式会社〇〇が新工場を建設", genreSlug: "business" })), {});
  assert.deepEqual(offerPlanForArticle(article({ title: "円安倒産、52カ月連続で株式会社の倒産が発生", genreSlug: "business", tags: ["倒産"] })), {});
});

test("在宅勤務の記事には回線を出す", () => {
  const plan = offerPlanForArticle(article({ title: "テレワークの通信環境、在宅勤務の会社員の半数が不満", genreSlug: "business" }));
  assert.ok(keys(plan.rail).some((key) => /hikari|wifi|wimax/.test(key)));
});

test("社名の一部（サイバーエージェント）でセキュリティやAIエージェントの広告を出さない", () => {
  const plan = offerPlanForArticle(article({ title: "ABEMA運営のサイバーエージェント、通期決算を発表へ", genreSlug: "business" }));
  assert.ok(!keys(plan.offer).some((key) => ["norton", "virusbuster", "eset", "surfshark", "xserver-vps", "doraverse"].includes(key)));
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
  for (const title of ["副業でブログを始める", "在宅勤務の回線", "新NISAの口座数が増加", "会議の議事録を文字起こし", "フィッシング詐欺が急増"]) {
    const offer = offerForArticle(article({ title, genreSlug: "business" }), 12) ?? offerForArticle(article({ title }), 12);
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
  const item = article({ title: "転職市場でAI人材の求人が増加", genreSlug: "business" });
  assert.equal(readNextForArticle(item)[0].href, "/choose/ai-career");
  assert.notEqual(readNextForArticle(item, ["/choose/ai-career"])[0].href, "/choose/ai-career");
  const phishing = article({ title: "金融庁、個人情報流出の相談窓口を開設", genreSlug: "public" });
  assert.equal(readNextForArticle(phishing)[0].href, "/compare/security-software");
});
