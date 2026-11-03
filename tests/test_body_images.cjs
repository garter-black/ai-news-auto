const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 本文に置く公式の写真・図（lib/body-images.ts）。2026-11-03 オーナー決定: 公式の図や写真を出典つきで本文に1〜2枚（台帳 I6）
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/body-images.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded._compile(compiled, sourcePath);
const { insertBodyImages, usableBodyImages, bodyImageHtml } = loaded.exports;

const IMG = { src: "https://ai-tech-express.jp/img/apple-q4-fig1-abcd1234.jpg", width: 1200, height: 675, kind: "dense", credit: "Apple",
  sourceUrl: "https://www.apple.com/jp/newsroom/2026/11/x/" };

test("出典（画像：会社名・公式発表へのリンク）を必ず出す", () => {
  const html = bodyImageHtml(IMG, "Apple、7〜9月期の売上高は1145億ドル");
  assert.match(html, /画像：Apple/);
  assert.match(html, /href="https:\/\/www\.apple\.com\/jp\/newsroom\/2026\/11\/x\/"/);
  assert.match(html, /loading="lazy"/);
  assert.match(html, /width="1200" height="675"/);
  assert.match(html, /alt="Appleの発表の図/);
});

test("自前の /img/ に置いた画像で、出典の名前があるものだけ・最大2枚", () => {
  const other = { ...IMG, src: "https://www.apple.com/newsroom/images/x.jpg" };
  const noCredit = { ...IMG, credit: " " };
  assert.equal(usableBodyImages([other, noCredit]).length, 0);
  assert.equal(usableBodyImages([IMG, IMG, IMG]).length, 2);
});

test("1枚目は最初の見出しの手前、2枚目は2つ目の見出しの手前", () => {
  const html = "<p>a</p><p>b</p><h2>日本の売上</h2><p>c</p><h2>見通し</h2><p>d</p>";
  const out = insertBodyImages(html, [IMG, { ...IMG, src: IMG.src.replace("fig1", "fig2") }], "t");
  const first = out.indexOf("fig1");
  const second = out.indexOf("fig2");
  assert.ok(first > 0 && first < out.indexOf("<h2>日本の売上"));
  assert.ok(second > out.indexOf("<h2>日本の売上") && second < out.indexOf("<h2>見通し"));
});

test("見出しの無い短い記事は2つ目の段落の後", () => {
  const out = insertBodyImages("<p>a</p><p>b</p><p>c</p>", [IMG], "t");
  assert.ok(out.indexOf("<figure") > out.indexOf("<p>b</p>") && out.indexOf("<figure") < out.indexOf("<p>c</p>"));
});

test("画像が無ければそのまま", () => {
  assert.equal(insertBodyImages("<p>a</p>", [], "t"), "<p>a</p>");
});
