const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 朝のまとめ・週のまとめの題名も題名の門番を通す（2026-11-03 オーナー決定・台帳 A25）。
// lib/headline.ts の gateHeadline / stripKnownDescriptors を、Python（scripts/headline_normalize.py）と同じ見本で確かめ、
// 同梱の号（content/digests）・週（content/weekly.json）を表示と同じ手順で通したときに、型どおりの題名が残らないことを確かめる
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/headline.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded._compile(compiled, sourcePath);
const { gateHeadline, stripKnownDescriptors, displayHeadline, SEO_QUESTION_RE } = loaded.exports;
const cases = JSON.parse(fs.readFileSync(path.join(root, "tests/fixtures/headline_gate_cases.json"), "utf8"));
const article = (slug) => {
  try {
    return JSON.parse(fs.readFileSync(path.join(root, "content/articles", `${slug}.json`), "utf8"));
  } catch {
    return null;
  }
};

test("説明つきの社名を外す（Python と同じ見本）", () => {
  for (const [before, after] of cases) assert.equal(stripKnownDescriptors(before), after, before);
});

test("検索向けの問いの題名は記事の見出しに替える", () => {
  assert.equal(gateHeadline("Mistral Large 4とは？日本で使える？料金も", "Mistral AI、「Mistral Large 4」の重みを公開"), "Mistral AI、「Mistral Large 4」の重みを公開");
  assert.equal(gateHeadline("ChatGPTのOpenAIが電力対策「Grid Protectors」", "x"), "OpenAIが電力対策「Grid Protectors」");
});

test("同梱の朝のまとめ・週のまとめを表示と同じ手順で通すと、型どおりの題名が残らない", () => {
  const titles = [];
  for (const name of fs.readdirSync(path.join(root, "content/digests")).filter((n) => /^\d{8}\.json$/.test(n))) {
    const data = JSON.parse(fs.readFileSync(path.join(root, "content/digests", name), "utf8"));
    for (const item of data.items) {
      const a = article(item.slug);
      titles.push(gateHeadline(item.headline || a?.title || "", a?.title));
    }
  }
  const weekly = JSON.parse(fs.readFileSync(path.join(root, "content/weekly.json"), "utf8"));
  for (const week of weekly.weeks) {
    for (const item of week.items) {
      const a = article(item.slug);
      titles.push(displayHeadline(a ? gateHeadline(a.title, a.title) : gateHeadline(item.title)));
    }
  }
  assert.ok(titles.length > 10);
  for (const t of titles) {
    assert.equal(stripKnownDescriptors(t), t, `説明つきの社名: ${t}`);
  }
  // 記事が残っている週の題名は、検索向けの問いの型にならない
  for (const week of weekly.weeks) {
    for (const item of week.items) {
      const a = article(item.slug);
      if (a) assert.ok(!SEO_QUESTION_RE.test(displayHeadline(gateHeadline(a.title, a.title))), a.title);
    }
  }
});

test("朝のまとめの要約の文も、説明つきの社名を外す（「ChatGPTのOpenAIが」。2026-11-03 本番で要約に残っていた）", () => {
  assert.equal(
    stripKnownDescriptors("ChatGPTのOpenAIが、新しく作るAI用の大きな施設で、電気が足りない日には使う量を減らすと発表しました。"),
    "OpenAIが、新しく作るAI用の大きな施設で、電気が足りない日には使う量を減らすと発表しました。",
  );
  const src = fs.readFileSync(path.join(__dirname, "../lib/digest.ts"), "utf8");
  assert.match(src, /summary: stripKnownDescriptors\(oneLineSummary\(article\)\)/);
});
