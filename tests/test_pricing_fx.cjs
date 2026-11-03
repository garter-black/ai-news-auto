const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const ts = require("typescript");

// 台帳 W21（2026-11-03 オーナー決定）: AI料金表は公式の料金ページを出典にし、出典と確認日を出す。
// 円換算の為替はサイト全体で1つ（content/pricing/_fx.json を lib/fx.ts から読む。同じ出典・同じ日付）。
const root = path.resolve(__dirname, "..");
const cache = new Map();

/** TS のモジュールを Next.js なしで読む（"@/..." はリポジトリの根から。JSON はそのまま） */
function load(file) {
  if (cache.has(file)) return cache.get(file).exports;
  if (file.endsWith(".json")) return JSON.parse(fs.readFileSync(file, "utf8"));
  const compiled = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true, resolveJsonModule: true },
  }).outputText;
  const m = new Module(file, module);
  m.filename = file;
  cache.set(file, m);
  m.require = (name) => {
    if (!name.startsWith("@/")) throw new Error(`unexpected import ${name}`);
    const base = path.join(root, name.slice(2));
    const found = [base, `${base}.ts`, `${base}.tsx`].find((p) => fs.existsSync(p) && fs.statSync(p).isFile());
    if (!found) throw new Error(`not found ${name}`);
    return load(found);
  };
  m._compile(compiled, file);
  return m.exports;
}

const official = load(path.join(root, "lib/official-pricing.ts"));
const fxMod = load(path.join(root, "lib/fx.ts"));
const stored = JSON.parse(fs.readFileSync(path.join(root, "content/pricing/_fx.json"), "utf8"));

test("早見表の行はすべて公式の料金ページが出典（自社の記事を出典にしない）・確認日つき", () => {
  const items = official.officialPriceItems();
  assert.ok(items.length >= 8, `行が少なすぎる: ${items.length}`);
  assert.ok(items.some((i) => i.kind === "api") && items.some((i) => i.kind === "plan"));
  for (const item of items) {
    assert.equal(item.source.slug, "", `${item.id} が記事を出典にしている`);
    assert.match(item.source.url, /^https:\/\//, `${item.id} に公式ページの URL が無い`);
    assert.doesNotMatch(item.source.url, /ai-tech-express\.jp/, `${item.id} が自社のページを出典にしている`);
    assert.match(item.source.publishedAt, /^\d{4}-\d{2}-\d{2}/, `${item.id} に確認日が無い`);
    if (item.kind === "api") assert.ok(item.input !== null || item.output !== null);
    if (item.kind === "plan") assert.ok(item.monthly > 0);
  }
});

test("日本で使えるかは確認表（content/japan_availability.json）にそろえる（Claude Pro を「未確認」にしない）", () => {
  const items = official.officialPriceItems();
  const claudePro = items.find((i) => i.id === "plan-claude-pro");
  assert.ok(claudePro, "Claude Pro の行が無い");
  assert.equal(claudePro.japan, "ok");
  const chatgptPlus = items.find((i) => i.id === "plan-chatgpt-plus");
  assert.ok(chatgptPlus && chatgptPlus.japan === "ok");
  assert.equal(official.japanOf("Perplexity Pro"), "unknown");
});

test("トップの案内と早見表のページは同じ確認日を出す", () => {
  const dates = official.officialPriceItems().map((i) => i.source.publishedAt).sort();
  assert.equal(official.officialPricingCheckedAt(), dates.at(-1));
  for (const file of ["components/HomeAssets.tsx", "app/ai-pricing/page.tsx"]) {
    assert.match(fs.readFileSync(path.join(root, file), "utf8"), /officialPricingCheckedAt\(\)/, `${file} が早見表と別の日付を出している`);
  }
});

test("為替はサイトで1つ: lib/fx.ts は _fx.json を読み、料金の段の表の fx も同じ日・同じ値", () => {
  const fx = fxMod.getUsdJpy();
  assert.equal(fx.rate, stored.rate);
  assert.equal(fx.date, stored.date);
  assert.equal(fx.source, stored.source);
  assert.match(fxMod.fxLabel(fx), /^1ドル＝\d+\.\d{2}円（\d{4}\/\d{2}\/\d{2}・.+）$/);
  for (const name of fs.readdirSync(path.join(root, "content/pricing"))) {
    if (!name.endsWith(".json") || name === "_fx.json") continue;
    const data = JSON.parse(fs.readFileSync(path.join(root, "content/pricing", name), "utf8"));
    if (!data.fx) continue;
    assert.equal(data.fx.rate, stored.rate, `${name} の為替が _fx.json と違う`);
    assert.equal(data.fx.date, stored.date, `${name} の為替の日付が _fx.json と違う`);
  }
});

test("ページ・部品に為替の決め打ちを置かない（AI料金表の 1ドル＝150円 のような値）", () => {
  const offenders = [];
  const walk = (dir) => {
    for (const ent of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
      const rel = path.join(dir, ent.name);
      if (ent.isDirectory()) walk(rel);
      else if (/\.(tsx?|mjs)$/.test(ent.name) && rel !== path.join("lib", "fx.ts")) {
        const text = fs.readFileSync(path.join(root, rel), "utf8");
        if (/useState\(\s*1[3-7]\d(?:\.\d+)?\s*\)|(?:rate|usdJpy|USD_JPY)\s*[:=]\s*1[3-7]\d(?:\.\d+)?\b|1ドル[＝=]\s*1[3-7]\d(?:\.\d+)?円/.test(text)) offenders.push(rel);
      }
    }
  };
  for (const dir of ["app", "components", "lib"]) walk(dir);
  assert.deepEqual(offenders, []);
  assert.match(fs.readFileSync(path.join(root, "lib/plan-prices.ts"), "utf8"), /getUsdJpy\(\)/);
  assert.match(fs.readFileSync(path.join(root, "lib/pricing-ladder.ts"), "utf8"), /getUsdJpy\(\)/);
});
