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
    article({ title: "トヨタのWECチーム、モンツァ6時間レースで最終戦へ", genreSlug: "sports", genre: "スポーツ", tags: ["モータースポーツ"] }),
    article({ title: "アバターSNSがTVアニメとコラボ", genreSlug: "entertainment", genre: "エンタメ", tags: ["アニメ"] }),
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
  const expected = { ai: "fallback-ai", science: "fallback-ai", business: "fallback-work", "tech-gadget": "fallback-gadget", investment: "fallback-money", "real-estate": "fallback-money", public: "fallback-ai", sports: "fallback-life", entertainment: "fallback-life", lifestyle: "fallback-life", other: "fallback-ai" };
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
  const pub = keys(offerPlanForArticle(article({ title: "遺族年金の受給者調査を実施", genreSlug: "public" })).offer);
  assert.ok(pub.indexOf("mf-kakutei") < pub.indexOf("matsui"));
  // お金の話題でない公共の記事に会計ソフトは出さない
  assert.ok(!keys(offerPlanForArticle(article({ title: "褒章の受章者が決定", genreSlug: "public" })).offer).includes("mf-kakutei"));
  assert.equal(keys(offerPlanForArticle(article({ title: "個人事業主から法人化する手順", genreSlug: "business" })).rail)[0], "yayoi-kaigyou");
  // 開業届・会社設立は独立・起業・税金の記事だけ。日銀の記事は証券、無関係なビジネス記事にも出さない
  assert.equal(keys(offerPlanForArticle(article({ title: "日銀の展望レポート", genreSlug: "business" })).rail)[0], "matsui");
  for (const title of ["Hondaが電動アシストマウンテンバイクを公開", "地ビールの出荷量が2年連続増加", "トヨタが水素の取り組みを発表"]) {
    const all = keys(offerPlanForArticle(article({ title, genreSlug: "business" })).offer);
    assert.ok(!all.some((key) => ["yayoi-kaigyou", "mf-setsuritsu", "yayoi-setsuritsu"].includes(key)), title);
  }
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

test("本文途中のあわせて読みたい（関連記事）は、直近30日の同じ話題の記事だけ。無ければ出さない", () => {
  const { inlineReadForArticle } = load(path.join(root, "lib/read-next.ts"));
  const now = Date.parse("2026-11-03T00:00:00Z");
  const day = 86_400_000;
  const at = (days) => new Date(now - days * day).toISOString();
  const company = (a) => a.channelSlug || "other";
  const src = article({ slug: "src", title: "フットサル日本代表、強豪モロッコと対戦", genreSlug: "sports", tags: ["フットサル", "フットサル日本代表", "スポーツ"], keywords: ["モロッコ"], publishedAt: at(0) });
  const same = article({ slug: "same", title: "フットサル日本代表、国内キャンプのメンバーを発表", genreSlug: "sports", tags: ["フットサル", "フットサル日本代表"], publishedAt: at(3) });
  const old = article({ slug: "old", title: "フットサル日本代表、強化試合の日程が決定", genreSlug: "sports", tags: ["フットサル", "フットサル日本代表"], publishedAt: at(45) });
  const loose = article({ slug: "loose", title: "なでしこジャパン、チケットを追加販売", genreSlug: "sports", tags: ["スポーツ", "サッカー"], publishedAt: at(1) });
  const other = article({ slug: "other", title: "フットサル日本代表の特集番組", genreSlug: "entertainment", tags: ["フットサル", "フットサル日本代表"], publishedAt: at(1) });
  const pool = [src, same, old, loose, other];
  assert.equal(inlineReadForArticle(src, pool, company, [], now)?.slug, "same");
  // 同じ話題でも30日より前・別ジャンル・関係の薄い記事は出さない
  assert.equal(inlineReadForArticle(src, [src, old, loose, other], company, [], now), undefined);
  // 記事末の「次に読むならこれ」と同じ記事は除ける
  assert.equal(inlineReadForArticle(src, pool, company, ["same"], now), undefined);
  // 語が1つ（「提携」など）当たっただけでは出さない
  const bike = article({ slug: "bike", title: "Hondaが電動マウンテンバイクを公開、独社と提携", genreSlug: "business", tags: ["Honda", "提携", "電動アシスト自転車"], publishedAt: at(0) });
  const oshi = article({ slug: "oshi", title: "JINENとzzz.inc.が業務提携、推し活の新事業", genreSlug: "business", tags: ["提携", "推し活"], publishedAt: at(1) });
  assert.equal(inlineReadForArticle(bike, [bike, oshi], company, [], now), undefined);
});

const HOSTING = new Set(offers.b2b.hostingPartners);
const topicOf = (key) => offers.topics.find((t) => t.key === key);

test("法人・企業向けの記事（ネットワーク・ゼロトラストなど）には個人向けのレンタルサーバー・VPSを出さない。既定はAIツール・スキル", () => {
  for (const item of [
    article({ title: "NTT西日本、企業向けゼロトラストの導入・運用支援サービスを開始", genreSlug: "business", genre: "ビジネス" }),
    article({ title: "マイクロソフトとLumen、企業向けAIネットワークを共同展開へ", genreSlug: "ai" }),
    article({ title: "法人向けWebサイト構築サービスを提供開始、企業の集客を支援", genreSlug: "business", genre: "ビジネス" }),
  ]) {
    const plan = offerPlanForArticle(item);
    const shown = [...keys(plan.rail), ...keys(plan.inline)];
    assert.ok(shown.length >= 1, item.title);
    assert.ok(shown.every((key) => !HOSTING.has(key)), `${item.title}: ${shown}`);
    assert.ok(readNextForArticle(item).every((link) => !["/compare/rental-server", "/compare/vps-dify-n8n"].includes(link.href)), item.title);
  }
  const zeroTrust = offerPlanForArticle(article({ title: "NTT西日本、企業向けゼロトラストの導入・運用支援サービスを開始", genreSlug: "business" }));
  assert.equal(zeroTrust.offer.topic, "fallback-b2b");
  assert.ok(keys(zeroTrust.rail).includes("doraverse"));
  // 話題がサーバー・セルフホストそのものなら、法人向けでもサーバーを出す
  const dify = offerPlanForArticle(article({ title: "企業向けにDifyのセルフホスト導入支援を開始、VPSで社内AIを構築", genreSlug: "ai" }));
  assert.ok([...keys(dify.rail), ...keys(dify.inline)].some((key) => HOSTING.has(key)));
  // 個人の副業の記事は従来どおり
  assert.ok(keys(offerPlanForArticle(article({ title: "会社員の副業で個人のホームページを開設する人が増加", genreSlug: "business" })).rail).includes("xserver"));
});

test("枠の見出しは、その枠に並ぶ商品の話題から作る（Geminiの見出しの下にVPNを出さない）", () => {
  const check = (offer, label) => {
    if (!offer) return;
    const topics = new Set(offer.items.map((item) => item.topic));
    if (topics.size === 1) {
      const key = [...topics][0];
      const topic = topicOf(key);
      if (topic) assert.equal(offer.headline, topic.headline, label);
      else assert.ok(String(key).startsWith("fallback-"), label);
    } else {
      // 複数の話題にまたがる枠は、並ぶ話題の名前から作った見出し
      for (const key of topics) {
        const topic = topicOf(key);
        if (topic && [...topics].indexOf(key) < 2) assert.ok(offer.headline.includes(topic.name), `${label}: ${offer.headline} / ${topic.name}`);
      }
    }
  };
  const gemini = article({ title: "Gemini、セキュリティ診断とプライバシー保護の新機能を追加", tags: ["Gemini", "セキュリティ", "VPN"], channelSlug: "google" });
  const plan = offerPlanForArticle(gemini);
  for (const box of [plan.rail, plan.inline]) {
    if (!box) continue;
    if (box.items.some((item) => item.key === "surfshark")) assert.notEqual(box.headline, topicOf("gemini").headline);
  }
  // 実データの全記事で、見出しと商品の話題がずれない・右側と本文中が重ならない・どの記事にも1つ以上
  const dir = path.join(root, "content/articles");
  for (const file of fs.readdirSync(dir).filter((f) => f.endsWith(".json"))) {
    const real = JSON.parse(fs.readFileSync(path.join(dir, file), "utf8"));
    const p = offerPlanForArticle(real);
    assert.ok(p.rail && p.rail.items.length >= 1, real.slug);
    check(p.rail, `${real.slug} rail`);
    check(p.inline, `${real.slug} inline`);
    const rail = new Set(keys(p.rail));
    assert.ok(keys(p.inline).every((key) => !rail.has(key)), real.slug);
    if (p.inline) assert.ok(p.inline.items.length >= 2, real.slug);
  }
});

test("製品・趣味の記事は、カテゴリの語で楽天（A8）とAmazonの検索リンクを出す（価格なし・PR枠）", () => {
  const cases = [
    [{ title: "Hondaが初の高級電動アシストマウンテンバイク「RN0」を公開", genreSlug: "business", tags: ["自転車"] }, "電動アシスト マウンテンバイク"],
    [{ title: "サムスンの折りたたみスマホ「Galaxy Z Fold8」の使い心地", genreSlug: "tech-gadget" }, "折りたたみスマホ"],
    [{ title: "任天堂、『ピクミン4 Nintendo Switch 2 Edition』の体験版を配信", genreSlug: "entertainment" }, "Nintendo Switch 2 ソフト"],
    [{ title: "日本卓球協会の全日本社会人選手権が閉幕", genreSlug: "sports" }, "卓球 ラケット"],
  ];
  for (const [fields, query] of cases) {
    const plan = offerPlanForArticle(article(fields));
    assert.equal(plan.offer.topic, "fallback-product", fields.title);
    assert.match(plan.rail.headline, /^この記事に関連する商品：/);
    const [rakuten, amazon] = plan.rail.items;
    assert.ok(rakuten.url.startsWith("https://rpx.a8.net/svt/ejp?a8mat=4BELTP+50ED2Q+2HOM+BWGDT&rakuten=y&a8ejpredirect="), rakuten.url);
    assert.ok(decodeURIComponent(decodeURIComponent(decodeURIComponent(rakuten.url))).includes(`search.rakuten.co.jp/search/mall/${query}/`), rakuten.url);
    assert.equal(amazon.url, `https://www.amazon.co.jp/s?k=${encodeURIComponent(query)}&tag=saisokuai-22`);
    assert.ok(plan.rail.items.every((item) => item.price == null && !item.priceText));
    assert.ok(plan.offer.pixels.includes("https://www15.a8.net/0.gif?a8mat=4BELTP+50ED2Q+2HOM+BWGDT"));
  }
  // 決算・株価などの記事は製品の記事として扱わない
  assert.notEqual(offerPlanForArticle(article({ title: "任天堂の決算、Switch 2が好調", genreSlug: "business" })).offer.topic, "fallback-product");
});

test("まとめ記事は脇の話題の1語でセキュリティソフトを出さず、AIツール・スキルの広告にする。デザインアプリの記事に VPS を出さない（2026-11-03 の画面の点検 D）", () => {
  const roundup = article({
    title: "GoogleのAI「Gemini 4」など10月の新発表まとめ　詐欺対策や偽画像の判別も",
    seoTitle: "Google AI 10月発表まとめ　Gemini 4とは？",
    tags: ["Gemini", "生成AI", "Google", "LLM", "セキュリティ", "量子コンピューター"],
    lede: "Googleが10月のAIの発表をまとめました。詐欺対策やセキュリティの新機能、偽画像の判別も入っています。",
  });
  const plan = offerPlanForArticle(roundup);
  assert.equal(plan.offer.topic, "gemini");
  const all = [...keys(plan.rail), ...keys(plan.inline)];
  assert.ok(all.length >= 1, "広告枠は必ず出す");
  for (const key of all) assert.ok(!/norton|eset|virusbuster|kaspersky|mcafee|nordvpn/.test(key), key);
  // 「あわせて読みたい」の案内も、まとめ記事にはセキュリティソフトの比較を出さない
  assert.ok(!readNextForArticle(roundup, [], 3).some((link) => link.href === "/compare/security-software"));
  // 同じ語でも、まとめでない記事ではセキュリティの話題を選べる
  const single = article({ title: "Googleが詐欺対策の新機能、偽画像を判別", tags: ["セキュリティ"] });
  assert.ok(offerPlanForArticle(single).offer.items.some((item) => item.topic === "security"));

  const canva = article({
    title: "Canvaがデザインアプリ「Affinity」iPad版を公開　描く・写真編集・ページ制作が1本に",
    tags: ["Affinity", "Canva", "iPad", "デザインアプリ", "写真編集"],
  });
  const canvaPlan = offerPlanForArticle(canva);
  assert.ok(canvaPlan.rail.items.length >= 1, "広告枠は必ず出す");
  for (const key of [...keys(canvaPlan.rail), ...keys(canvaPlan.inline)]) assert.ok(!/xserver|conoha|sakura|lolipop/.test(key), key);
});
