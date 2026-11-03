const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 一覧・カードの見出し（lib/headline.ts）。40字を超える見出しは最初の区切りまでの前半だけを出す。
const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, "lib/headline.ts");
const compiled = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
const loaded = new Module(sourcePath, module);
loaded.filename = sourcePath;
loaded._compile(compiled, sourcePath);
const { displayHeadline, headOf, splitHeadline } = loaded.exports;

test("40字を超える見出しは一覧で前半だけ。40字以内・区切りが無いものは全文", () => {
  const long = "MicrosoftのAI秘書「Copilot Cowork」、9人が1日1,211件の更新　AIに書かせる開発の6つの教訓";
  assert.equal(displayHeadline(long), "MicrosoftのAI秘書「Copilot Cowork」、9人が1日1,211件の更新");
  assert.equal(displayHeadline("Apple、7〜9月期売上高が過去最高　新iPhone好調"), "Apple、7〜9月期売上高が過去最高　新iPhone好調");
  const noSep = "あ".repeat(45);
  assert.equal(displayHeadline(noSep), noSep);
  // 英字どうしの間の空白では切らない・前半が短すぎるときは切らない
  assert.equal(headOf("新AI「Gemini 4 Argon」を発表"), null);
  assert.equal(headOf("短い前半　とても長い後半の文章がここに続きます"), null);
});

test("全角の空白のあとは補足として別の行に出す（かっこの中の空白・短すぎる前半では分けない）", () => {
  assert.deepEqual(splitHeadline("NVIDIA、AI学習の自動復旧ツールを公開　障害で止まった学習を自動で再開"), {
    main: "NVIDIA、AI学習の自動復旧ツールを公開",
    sub: "障害で止まった学習を自動で再開",
  });
  assert.deepEqual(splitHeadline("新AI「Gemini　4」を発表した会社の話"), { main: "新AI「Gemini　4」を発表した会社の話", sub: null });
  assert.deepEqual(splitHeadline("短い前半　後半の文章"), { main: "短い前半　後半の文章", sub: null });
  assert.deepEqual(splitHeadline("区切りの無い見出しはそのまま"), { main: "区切りの無い見出しはそのまま", sub: null });
});
