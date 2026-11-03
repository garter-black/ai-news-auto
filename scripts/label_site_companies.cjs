// サイトの企業の札（lib/companies.ts の companyOf）を、記事ごとに JSON で出す（scripts/label_check.py が Haiku の答えと比べる）。
// 使い方: node scripts/label_site_companies.cjs  → {"names": {企業名: slug}, "articles": {記事 slug: 企業 slug}}
// lib/*.ts を typescript で CommonJS に直して読む（@/ はリポジトリの根）。content/index.json が無ければ先に node scripts/build_index.mjs
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const ts = require("typescript");

const root = path.resolve(__dirname, "..");
const cache = new Map();

function resolve(from, id) {
  const base = id.startsWith("@/") ? path.join(root, id.slice(2)) : path.resolve(path.dirname(from), id);
  for (const ext of ["", ".ts", ".tsx", ".json", ".mjs"]) {
    const p = base + ext;
    if (fs.existsSync(p) && fs.statSync(p).isFile()) return p;
  }
  return null;
}

function load(file) {
  if (cache.has(file)) return cache.get(file).exports;
  const m = new Module(file);
  m.filename = file;
  m.paths = Module._nodeModulePaths(path.dirname(file));
  cache.set(file, m);
  const req = m.require.bind(m);
  m.require = (id) => {
    if (id.startsWith("@/") || id.startsWith(".")) {
      const p = resolve(file, id);
      if (p?.endsWith(".json")) return JSON.parse(fs.readFileSync(p, "utf8"));
      if (p && /\.tsx?$/.test(p)) return load(p);
      if (p) return req(p);
    }
    return req(id);
  };
  const out = ts.transpileModule(fs.readFileSync(file, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
  }).outputText;
  m._compile(out, file);
  return m.exports;
}

const { COMPANIES, OTHER, companyOf } = load(path.join(root, "lib/companies.ts"));
const index = JSON.parse(fs.readFileSync(path.join(root, "content/index.json"), "utf8")).articles;
const names = {};
for (const c of COMPANIES) {
  names[c.name] = c.slug;
  names[c.name.replace(/（.*）$/, "")] = c.slug;
}
names.AWS = "amazon";
names["サムスン"] = "samsung";
names["サムスン電子"] = "samsung";
names.Mistral = "mistral";
names["Google DeepMind"] = "google";
names.DeepMind = "google";
names.GitHub = "microsoft";
const articles = Object.fromEntries(index.map((a) => [a.slug, companyOf(a).slug]));
process.stdout.write(JSON.stringify({ names, other: OTHER.slug, articles }));
