const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 台帳 W22（2026-11-03 オーナー決定）: 取り下げた記事の 410 ページはサイトの見た目（ヘッダー・ロゴ・フッター）で、
// 丁寧な説明と、今も載っている近い記事へのリンクを出す。状態コードは 410 のまま、noindex。
const root = path.resolve(__dirname, "..");
const read = (rel) => fs.readFileSync(path.join(root, rel), "utf8");
let live = [];
const file = path.join(root, "lib/retired.ts");
const out = ts.transpileModule(read("lib/retired.ts"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
const m = new Module(file, module);
m.filename = file;
m.require = (name) => {
  if (name === "@/content/retired.json") return { articles: { "old-gemini-video": { title: "Gemini 4の新機能を動画で解説", channel: "creator-x", reason: "creator-video" } } };
  if (name === "@/lib/articles") return { getAllArticles: () => live };
  throw new Error(name);
};
m._compile(out, file);
const { getRetired, relatedLiveArticles, titleTokens } = m.exports;
const art = (slug, title, extra = {}) => ({ slug, title, tags: [], type: "news", genreSlug: "ai", channelSlug: "google", publishedAt: "2026-11-01T00:00:00Z", ...extra });

test("取り下げた記事の情報を slug で引く（無い slug は undefined）", () => {
  assert.equal(getRetired("old-gemini-video").reason, "creator-video");
  assert.equal(getRetired("no-such"), undefined);
  assert.equal(getRetired("constructor"), undefined);
});

test("近い記事は題名の言葉・発信元の重なりで選び、関係ない記事を混ぜない", () => {
  live = [
    art("gemini-4", "Gemini 4 Argonの料金が下がる"),
    art("gemini-carbon", "Gemini 4 Carbonを公開", { publishedAt: "2026-11-02T00:00:00Z" }),
    art("quake", "【地震】千葉県で震度5弱", { genreSlug: "public", channelSlug: "jma" }),
    art("dup", "Gemini 4の重複", { duplicate: true }),
  ];
  const picked = relatedLiveArticles(getRetired("old-gemini-video")).map((a) => a.slug);
  assert.deepEqual(picked, ["gemini-carbon", "gemini-4"]);
  assert.ok(titleTokens("Gemini 4の新機能を動画で解説").every((t) => t.word !== "新機能"));
});

test("近い記事が無いときは、新しい記事で2〜3本埋める（行き止まりにしない）", () => {
  live = [art("a", "OpenAIの発表"), art("b", "Claudeの発表", { publishedAt: "2026-11-02T00:00:00Z" }), art("c", "地震", { genreSlug: "public" })];
  const picked = relatedLiveArticles({ title: "まったく関係のない話題", channel: "none" });
  assert.ok(picked.length >= 2 && picked.length <= 3);
  assert.equal(picked[0].slug, "b");
});

test("410 はサイトの記事ページ（components/GoneView.tsx）を同じ URL で描いて返す。noindex", () => {
  const worker = read("cloudflare/worker.ts");
  assert.match(worker, /render\(new Request\(request\.url/, "410 で同じ URL のサイトのページを描いていない");
  assert.match(worker, /html\.includes\("data-gone-page"\)\) return new Response\(staticHtml\(html\), \{ status: 410, headers \}\)/);
  // 410 の文書はハイドレーションするとエラー（#418）になるので、Next の読み込み用スクリプトを外した静的なページにする
  const fn = /function staticHtml\(html: string\): string \{([\s\S]*?)\n\}/.exec(worker);
  assert.ok(fn, "staticHtml が無い");
  const staticHtml = new Function("html", fn[1].replace(/\\\//g, "\\/"));
  const sample = '<link rel="preload" as="script" href="/_next/static/chunks/a.js"/><script src="/_next/static/chunks/a.js" async=""></script><script>(self.__next_f=self.__next_f||[]).push([0])</script><script>self.__next_f.push([1,"x"])</script><script id="_R_">(globalThis["TURBOPACK"]||[]).push({})</script><script type="application/ld+json">{}</script><header class="site-header"></header>';
  const out = staticHtml(sample);
  assert.doesNotMatch(out, /_next\/static|__next_f|TURBOPACK/);
  assert.match(out, /application\/ld\+json/);
  assert.match(out, /site-header/);
  assert.match(worker, /"X-Robots-Tag": "noindex"/);
  const page = read("app/articles/[slug]/page.tsx");
  assert.match(page, /getRetired\(slug\)/);
  assert.match(page, /<GoneView entry=\{retired\} \/>/);
  assert.match(page, /robots: \{ index: false/);
  const view = read("components/GoneView.tsx");
  assert.match(view, /この記事は掲載を終了しました/);
  assert.match(view, /relatedLiveArticles\(entry\)/);
  assert.match(view, /data-gone-page/);
  // サイトのヘッダー・フッターは app/layout.tsx から付く
  assert.match(read("app/layout.tsx"), /<Header \/>[\s\S]*<Footer \/>/);
  // 責める言い方をしない
  const gone = /const GONE_HTML = `([^`]*)`/.exec(worker)[1];
  const shown = view.replace(/\/\*[\s\S]*?\*\//g, "");
  assert.doesNotMatch(gone + shown, /取り下げました|410 Gone/);
});
