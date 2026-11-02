#!/usr/bin/env node
/**
 * SNS カード（写真だけ・p1）の点検。新しい記事 N 本で、写真の主役（imageFocus）が
 *   1. カードの外にはみ出して切れていないか
 *   2. 角のロゴ札・注記の下になっていないか
 * を、カードと同じ計算（lib/card-geometry.ts）で確かめる。画像は描かないので速い。
 *
 *   node scripts/check_card_crop.mjs [本数=30]
 * 主役の位置が無い記事は「写真全体が主役」とみなす（札が写真に重なるので、件数だけ知らせる）。
 */
import fs from "node:fs";
import path from "node:path";
import { CARD_H, CARD_W, NOTE, TAG, cornerRect, overlap, photoOnlyLayout } from "../lib/card-geometry.ts";

const limit = Number(process.argv[2] || 30);
const dir = path.join(process.cwd(), "content", "articles");
const articles = fs
  .readdirSync(dir)
  .filter((f) => f.endsWith(".json"))
  .map((f) => {
    try {
      return JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    } catch {
      return null;
    }
  })
  .filter((a) => a && a.thumbnail)
  .sort((a, b) => String(b.generatedAt || b.publishedAt).localeCompare(String(a.generatedAt || a.publishedAt)))
  .slice(0, limit);

const NOTE_KINDS = new Set(["ai", "stock", "official", "figure"]);
let bad = 0;
let unknown = 0;
const rows = [];
for (const a of articles) {
  const hasFocus = a.imageFocus && a.imageFocus.src === a.thumbnail;
  const layout = photoOnlyLayout(a, NOTE_KINDS.has(a.imageCredit?.kind));
  const s = layout.subject;
  const problems = [];
  const eps = 1;
  if (s.x < -eps || s.y < -eps || s.x + s.w > CARD_W + eps || s.y + s.h > CARD_H + eps) problems.push("主役がカードの外で切れる");
  const c = layout.core;
  if (overlap(cornerRect(layout.tag, TAG), c) > 0) problems.push(`ロゴ札(${layout.tag})が主役にかかる`);
  if (layout.note && overlap(cornerRect(layout.note, NOTE), c) > 0) problems.push(`注記(${layout.note})が主役にかかる`);
  if (!hasFocus) unknown++;
  if (problems.length && hasFocus) bad++;
  rows.push({ slug: a.slug, fit: layout.mode, tag: layout.tag, note: layout.note, problems: hasFocus ? problems : ["主役の位置なし（写真全体を主役とみなす）"] });
}
for (const r of rows) console.log(`${r.problems.length ? "NG" : "OK"} ${r.fit.padEnd(7)} tag=${r.tag} note=${r.note ?? "-"} ${r.slug} ${r.problems.join(" / ")}`);
console.log(`\n${rows.length} 本中 NG ${bad} 本（主役の位置なし ${unknown} 本）`);
process.exit(bad ? 1 : 0);
