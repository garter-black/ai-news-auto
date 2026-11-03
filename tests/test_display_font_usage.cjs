// ロゴ用の Dela Gothic（font-display）はロゴの文字だけを含む。見出しなどの任意の文に使うと、
// 一部の文字（ス・の など）だけ書体が変わって崩れる。使ってよい場所を決めて、それ以外を落とす。
const fs = require("fs");
const path = require("path");
const ALLOWED = new Set([
  "components/Header.tsx",
  "components/Thumb.tsx",
  "components/Footer.tsx",
  "components/rail/RankingBody.tsx",
]);
const root = path.join(__dirname, "..");
const bad = [];
function walk(dir) {
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    if (fs.statSync(p).isDirectory()) { if (name !== "node_modules") walk(p); continue; }
    if (!/\.(tsx|ts)$/.test(name)) continue;
    const rel = path.relative(root, p).split(path.sep).join("/");
    if (fs.readFileSync(p, "utf8").includes("font-display") && !ALLOWED.has(rel)) bad.push(rel);
  }
}
for (const d of ["components", "app", "lib"]) walk(path.join(root, d));
if (bad.length) {
  console.error("font-display（ロゴ用の書体）を使ってはいけない場所で使っています:", bad);
  process.exit(1);
}
console.log("ok: font-display はロゴの場所だけ");
