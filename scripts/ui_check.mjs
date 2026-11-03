#!/usr/bin/env node
/**
 * 画面の崩れを本物のブラウザで見張る（読者が気づく小さな崩れを、オーナーより先に見つける）。
 *
 * 使い方:
 *   node scripts/ui_check.mjs                               # 本番の主要ページ（CI と同じ。数分）
 *   node scripts/ui_check.mjs --set full                    # すべてのページの種類（手動の点検向け）
 *   node scripts/ui_check.mjs --base http://localhost:3100 --paths /,/articles
 *   node scripts/ui_check.mjs --out out/ui_check            # スクショ・report.json・report.md の置き場所
 *
 * 375×812（スマホ）と 1440×900（PC）× ライト／ダーク で開き、次のどれかがあれば失敗（終了コード 1）:
 *   - 読めない画像（naturalWidth が 0）
 *   - 横にはみ出す（スマホで横スクロールが出る）／要素が画面や親の枠からはみ出す・文字が切れる
 *   - 見えている文字に undefined / NaN / null / [object Object]、生の Markdown・HTML（** ## [..](..) <div> &amp;）
 *   - コンソールのエラー・ページの JS エラー・自サイトの読み込み失敗（4xx/5xx）
 *   - ロゴ用の書体（Dela Gothic）を決まった文言以外に使う（入っていない字だけ書体が変わる。scripts/subset_dela_font.py）
 *   - 同じ中身の区切り（表・節）が2回続く
 *   - 提携リンク（rel=sponsored）の枠に PR／広告 の表示がない
 *   - 読み込み時のレイアウトのずれ（CLS）が 0.1 を超える
 * あわせて、動画の再生ボタン・ストーリーの開閉・計算ツールの入力・検索・共有ボタン・テーマ切替が動くか、
 * /card・/cover の画像の大きさ、RSS の形、内部リンク（最大 100 本）の切れを確かめる。
 *
 * 守ること（docs/automation.md）: 自社のしるし（?_internal=1 とヘッダー X-Saisoku-Internal: 1）を付け、
 * A8・楽天・Amazon・AdSense・計測への通信は止める。提携リンクは押さない・取りに行かない。
 *
 * 依存: playwright-core（CI では `npm i --no-save playwright-core` で入れ、ランナーの Chrome を使う）。
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

// ---------------------------------------------------------------------------
// 引数
// ---------------------------------------------------------------------------
const argv = process.argv.slice(2);
const arg = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
const BASE = arg("base", process.env.UI_CHECK_BASE || "https://ai-tech-express.jp").replace(/\/$/, "");
const SET = arg("set", "ci");
const OUT = arg("out", join(ROOT, "out", "ui_check"));
const CONCURRENCY = Number(arg("concurrency", "4"));
const SHOTS = !flag("no-shots");
const LINK_LIMIT = Number(arg("links", "100"));
const ONLY_PATHS = arg("paths", "");
const CHROME =
  process.env.CHROME_PATH ||
  ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium"].find((p) => existsSync(p));

// ---------------------------------------------------------------------------
// 決まり
// ---------------------------------------------------------------------------
/** 止める通信（docs/automation.md）。広告・計測は数字を水増ししないよう一切読まない */
const BLOCKED_HOST =
  /(^|\.)(a8\.net|afl\.rakuten\.co\.jp|rakuten\.co\.jp|amazon\.co\.jp|amzn\.to|amazon-adsystem\.com|media-amazon\.com|ssl-images-amazon\.com|googlesyndication\.com|doubleclick\.net|googleadservices\.com|adtrafficquality\.google|adservice\.google\.com|cloudflareinsights\.com|google-analytics\.com|googletagmanager\.com|facebook\.net|connect\.facebook\.net)$/i;
const VIEWPORTS = [
  { name: "mobile", width: 375, height: 812, isMobile: true, hasTouch: true, deviceScaleFactor: 2 },
  { name: "desktop", width: 1440, height: 900, isMobile: false, hasTouch: false, deviceScaleFactor: 1 },
];
const THEMES = ["light", "dark"];
const CLS_LIMIT = 0.1;

/** ロゴ用の書体で出してよい文言（scripts/subset_dela_font.py の TEXTS と英数字）。ここから外れた字は別の書体になって崩れる */
function delaAllowed() {
  const src = readFileSync(join(ROOT, "scripts", "subset_dela_font.py"), "utf8");
  const block = src.match(/TEXTS\s*=\s*\[([\s\S]*?)\]/);
  const texts = block ? [...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : ["サイソク"];
  return { texts, chars: [...new Set(texts.join(""))].join("") };
}

// ---------------------------------------------------------------------------
// 調べるページ（記事は repo の content から種類ごとに選び、公開済み＝RSS にあるものを優先）
// ---------------------------------------------------------------------------
function readJsonDir(dir) {
  const full = join(ROOT, dir);
  if (!existsSync(full)) return [];
  return readdirSync(full)
    .filter((f) => f.endsWith(".json"))
    .map((f) => {
      try {
        return { file: f, ...JSON.parse(readFileSync(join(full, f), "utf8")) };
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

async function fetchText(url) {
  try {
    const res = await fetch(url, { headers: { "X-Saisoku-Internal": "1", "User-Agent": "saisoku-ui-check" }, redirect: "follow" });
    return { status: res.status, type: res.headers.get("content-type") || "", text: await res.text(), url: res.url };
  } catch (e) {
    return { status: 0, type: "", text: String(e), url };
  }
}

async function pickArticles(full) {
  const feed = await fetchText(`${BASE}/feed.xml?_internal=1`);
  const live = new Set([...feed.text.matchAll(/\/articles\/([^<"?#\s]+)/g)].map((m) => decodeURIComponent(m[1])));
  const cutoff = Date.now() - 30 * 60 * 1000; // 30 分より新しい記事はまだ公開されていないことがある
  const all = readJsonDir("content/articles")
    .filter((a) => a.slug && !a.duplicateOf && Date.parse(a.generatedAt || 0) < cutoff)
    .sort((a, b) => String(b.generatedAt).localeCompare(String(a.generatedAt)));
  const pool = live.size ? [...all.filter((a) => live.has(a.slug)), ...all.filter((a) => !live.has(a.slug))] : all;
  const isAiImage = (a) => a.imageCredit && typeof a.imageCredit === "object" && a.imageCredit.kind === "ai";
  const kinds = [
    ["最新の記事", () => true],
    ["AI", (a) => a.genreSlug === "ai" && !a.breaking],
    ["ビジネス", (a) => a.genreSlug === "business"],
    ["お金・税・制度", (a) => ["public", "investment", "real-estate"].includes(a.genreSlug)],
    ["他社比較の表", (a) => Array.isArray(a.rivals) ? a.rivals.length : a.rivals],
    ["スポーツ", (a) => a.genreSlug === "sports"],
    ["速報", (a) => a.breaking],
    ["解説動画", (a) => a.video && a.video.src],
    ["グラフ・計算", (a) => a.charts && (Array.isArray(a.charts) ? a.charts.length : true)],
    ["FAQ", (a) => Array.isArray(a.faq) && a.faq.length],
    ["AI画像の注記", isAiImage],
    ["料金の段の表", (a) => a.pricingProduct],
    ["YouTube 元動画", (a) => a.videoId && !a.sourceType],
  ];
  const chosen = [];
  const used = new Set();
  for (const [label, pred] of kinds) {
    if (!full && ["AI", "ビジネス", "スポーツ", "YouTube 元動画"].includes(label)) continue;
    const a = pool.find((x) => pred(x) && !used.has(x.slug)) || pool.find((x) => pred(x));
    if (!a) continue;
    used.add(a.slug);
    chosen.push({ path: `/articles/${a.slug}`, label: `記事（${label}）`, kind: "article", article: a });
  }
  return chosen;
}

async function buildPages() {
  if (ONLY_PATHS) {
    return ONLY_PATHS.split(",").map((raw) => {
      const p = raw.trim();
      const kind = p === "/" ? "home" : p.startsWith("/articles/") ? "article" : p.startsWith("/search") ? "search" : p.startsWith("/tools/") ? "tool" : /does-not-exist/.test(p) ? "404" : "page";
      return { path: p, label: p, kind };
    });
  }
  const full = SET === "full";
  const pages = [
    { path: "/", label: "トップ", kind: "home" },
    { path: "/articles", label: "記事一覧", kind: "list" },
  ];
  // ジャンルはヘッダーのタブに出ているものだけ（記事の無いジャンルはタブにも無く 404 が正しい。決め打ちだと不動産の 404 を誤って数えていた。2026-11-04）
  let genres = ["ai", "business", "investment", "public", "tech-gadget", "sports", "lifestyle", "entertainment"];
  try {
    const res = await fetch(`${BASE}/?_internal=1`, { headers: { "X-Saisoku-Internal": "1" } });
    const linked = [...new Set([...(await res.text()).matchAll(/href="\/genres\/([a-z-]+)"/g)].map((m) => m[1]))];
    if (linked.length) genres = linked;
  } catch {
    // 取れなければ決め打ちの一覧
  }
  for (const g of full ? genres : genres.filter((g) => ["ai", "investment"].includes(g))) pages.push({ path: `/genres/${g}`, label: `ジャンル ${g}`, kind: "list" });
  pages.push(...(await pickArticles(full)));
  const queries = full ? ["ChatGPT", "確定申告", "大谷"] : ["ChatGPT"];
  for (const q of queries) pages.push({ path: `/search?q=${encodeURIComponent(q)}`, label: `検索「${q}」`, kind: "search" });
  // 計算ツールの一覧（/tools）は防衛特別所得税のページへ転送（2026-11-03 台帳 W12）。図鑑の一覧と製品ページを見る
  pages.push({ path: "/products", label: "AIサービス図鑑", kind: "list" });
  pages.push({ path: "/products/codex", label: "図鑑 Codex", kind: "page" });
  const tools = existsSync(join(ROOT, "app/tools")) ? readdirSync(join(ROOT, "app/tools")).filter((d) => !d.includes(".")) : [];
  for (const t of full ? tools : tools.slice(0, 1)) pages.push({ path: `/tools/${t}`, label: `ツール ${t}`, kind: "tool" });
  pages.push({ path: "/ai-pricing", label: "AI料金表", kind: "page" });
  pages.push({ path: "/guides", label: "使い方ガイド一覧", kind: "list" });
  const guides = readJsonDir("content/guides").map((g) => g.slug || g.file.replace(/\.json$/, ""));
  const howtos = readJsonDir("content/howto").map((g) => g.slug || g.file.replace(/\.json$/, ""));
  for (const s of full ? [...guides.slice(0, 3), ...howtos.slice(0, 4)] : [guides[0], howtos[0]].filter(Boolean)) {
    pages.push({ path: `/guides/${s}`, label: `ガイド ${s}`, kind: "page" });
  }
  const chooseSrc = ["lib/choose-data.ts", "lib/choose-dify.ts"]
    .filter((f) => existsSync(join(ROOT, f)))
    .map((f) => readFileSync(join(ROOT, f), "utf8"))
    .join("\n");
  const chooses = [...new Set([...chooseSrc.matchAll(/^\s*slug:\s*"([a-z0-9-]+)"/gm)].map((m) => m[1]))];
  for (const s of full ? chooses : chooses.slice(0, 1)) pages.push({ path: `/choose/${s}`, label: `選び方 ${s}`, kind: "page" });
  const digests = readJsonDir("content/digests").map((d) => d.file.replace(/\.json$/, "")).sort();
  if (digests.length) pages.push({ path: `/digest/${digests.at(-1)}`, label: "朝のまとめ（最新）", kind: "page" });
  const legal = full ? ["/about", "/editorial-policy", "/contact", "/disclaimer", "/privacy"] : ["/about"];
  for (const p of legal) pages.push({ path: p, label: p, kind: "page" });
  pages.push({ path: "/this-page-does-not-exist-ui-check", label: "404 ページ", kind: "404" });
  return pages;
}

// ---------------------------------------------------------------------------
// ページの中で調べること（ブラウザの中で動く）
// ---------------------------------------------------------------------------
/* eslint-disable */
function inPageAudit(opts) {
  const { dela, viewportWidth } = opts;
  const out = { brokenImages: [], fallbacks: [], overflow: [], badText: [], fontLeaks: [], duplicates: [], prMissing: [], missingAlt: [], emptyBoxes: [], stretched: [], repeatedSlots: [], noData: [] };
  const vis = (el) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const path = (el) => {
    const parts = [];
    for (let e = el; e && e !== document.body && parts.length < 4; e = e.parentElement) {
      let s = e.tagName.toLowerCase();
      if (e.id) s += `#${e.id}`;
      else if (typeof e.className === "string" && e.className.trim()) s += "." + e.className.trim().split(/\s+/).slice(0, 2).join(".");
      parts.unshift(s);
    }
    return parts.join(" > ");
  };
  const snippet = (t) => (t || "").replace(/\s+/g, " ").trim().slice(0, 90);
  const isClosedDetails = (el) => { const d = el.closest("details:not([open])"); return d && !el.closest("summary"); };

  // 1. 画像
  // 読めなかった画像は、壊れたアイコンを見せないよう app/layout.tsx の受け皿が透明な画像・記事のカバーに替え、
  // サムネイル（components/Thumb.tsx）は代わりの図版にする。naturalWidth だけでは見つからないので、その印も数える
  const BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP";
  for (const img of document.images) {
    const src = img.currentSrc || img.src || "";
    // 広告の素材は止めているので読めないのが当たり前（見ない）
    if (img.closest('a[rel~="sponsored"], [data-aff-pos], aside[aria-label*="PR"]')) continue;
    if (src.startsWith(BLANK) && vis(img)) { out.brokenImages.push({ src: "（読めずに透明な画像へ差し替え）", where: path(img) }); continue; }
    if (img.hasAttribute("data-fell") && vis(img)) out.fallbacks.push({ src: src.slice(0, 120), where: path(img), why: "読めずに記事のカバーへ差し替え" });
    if (img.getAttribute("data-thumb-stage") === "cover" && vis(img)) out.fallbacks.push({ src: src.slice(0, 120), where: path(img), why: "サムネイルが読めずカバーへ差し替え" });
    if (!src || src.startsWith("data:")) continue;
    let host = "";
    try { host = new URL(src, location.href).hostname; } catch {}
    if (opts.blocked.some((re) => new RegExp(re, "i").test(host))) continue; // 止めた広告の画像は見ない
    if (!vis(img) && img.loading === "lazy" && !img.complete) continue;
    if (img.complete && img.naturalWidth === 0) {
      out.brokenImages.push({ src: src.slice(0, 160), where: path(img) });
      continue;
    }
    if (!img.alt && img.getAttribute("alt") === null && vis(img)) out.missingAlt.push({ src: src.slice(0, 120), where: path(img) });
    // 引き伸ばし・縦横比の崩れ（object-fit が fill のまま縦横比が 15% 以上違う）
    if (img.complete && img.naturalWidth > 0 && vis(img)) {
      const r = img.getBoundingClientRect();
      const fit = getComputedStyle(img).objectFit;
      if (fit === "fill" && r.width > 40 && r.height > 40) {
        const a = r.width / r.height, n = img.naturalWidth / img.naturalHeight;
        if (Math.abs(a / n - 1) > 0.15) out.stretched.push({ src: src.slice(0, 120), where: path(img), shown: `${Math.round(r.width)}x${Math.round(r.height)}`, natural: `${img.naturalWidth}x${img.naturalHeight}` });
      }
      // 小さな元画像を大きく引き伸ばしてぼやける（表示幅が元の幅×端末の倍率の 2 倍を超える）
      if (r.width > 200 && img.naturalWidth * 2 < r.width) out.stretched.push({ src: src.slice(0, 120), where: path(img), shown: `${Math.round(r.width)}x${Math.round(r.height)}`, natural: `${img.naturalWidth}x${img.naturalHeight}`, blurry: true });
    }
  }

  for (const el of document.querySelectorAll('[data-thumb-failed="error"]')) {
    if (vis(el)) out.brokenImages.push({ src: "（サムネイルが読めず代わりの図版）", where: path(el.parentElement || el) });
  }

  // 2. はみ出し
  const doc = document.documentElement;
  if (doc.scrollWidth > doc.clientWidth + 1) out.overflow.push({ kind: "page", detail: `横スクロール ${doc.scrollWidth}px > ${doc.clientWidth}px` });
  const scrollableX = (el) => {
    for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (/(auto|scroll)/.test(s.overflowX)) return true;
      if (s.position === "fixed" && (e.getBoundingClientRect().left < -5 || e.getBoundingClientRect().right > viewportWidth + 5)) return true; // 画面外に隠したメニュー
    }
    return false;
  };
  const all = [...document.body.querySelectorAll("*")];
  let reported = 0;
  for (const el of all) {
    if (reported > 12) break;
    if (["SCRIPT", "STYLE", "NOSCRIPT", "svg", "path", "BR"].includes(el.tagName)) continue;
    if (!vis(el) || isClosedDetails(el)) continue;
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    if (s.position === "fixed" && (r.right <= 0 || r.left >= viewportWidth)) continue;
    // 画面の右にはみ出す（横に流す枠の中は除く）
    if (r.right > viewportWidth + 1 && r.left < viewportWidth && !scrollableX(el) && el.closest("[aria-hidden='true'], .sr-only") === null) {
      const parent = el.parentElement;
      if (parent && parent.getBoundingClientRect().right > viewportWidth + 1 && !scrollableX(parent)) continue; // いちばん外側の 1 つだけ出す
      out.overflow.push({ kind: "viewport", where: path(el), detail: `右端 ${Math.round(r.right)}px > ${viewportWidth}px`, text: snippet(el.innerText) });
      reported++;
      continue;
    }
    // 文字が枠で切れる（1 行・省略記号なし・はみ出しを隠している）
    const own = (el.textContent || "").trim();
    const srOnly = el.closest(".sr-only") || (s.position === "absolute" && (s.clip !== "auto" || s.clipPath !== "none" || r.width <= 1 || r.height <= 1));
    const symbols = /^[★☆●○◆◇■□▲△▼▽…・\s]+$/.test(own); // 星の数などの塗り分け（わざと切っている）
    if (!srOnly && !symbols && /(hidden|clip)/.test(s.overflowX) && s.textOverflow !== "ellipsis" && !s.webkitLineClamp?.match(/\d/) && el.children.length === 0 && own.length > 1) {
      if (el.scrollWidth > el.clientWidth + 2 && s.whiteSpace.startsWith("nowrap")) {
        out.overflow.push({ kind: "clipped", where: path(el), detail: `文字が切れる ${el.scrollWidth}px > ${el.clientWidth}px`, text: snippet(el.textContent) });
        reported++;
      }
    }
  }

  // 3. 見えている文字のおかしな言葉・生の Markdown/HTML
  const main = document.querySelector("main") || document.body;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const BAD = [
    [/(^|[^A-Za-z_\-./])(undefined|NaN|null|\[object Object\])(?![A-Za-z_\-])/, "undefined/NaN/null"],
    [/\*\*[^*\s][^*]{0,80}\*\*|(^|\s)\*\*(\s|$)/, "生の **太字**"],
    [/(^|\n)\s{0,3}#{1,6}\s+\S/, "生の ## 見出し"],
    [/\[[^\]\n]{1,80}\]\((https?:\/\/|\/)[^)\s]+\)/, "生の [リンク](URL)"],
    [/<\/?(div|span|p|br|strong|a|table|tr|td|ul|li|h[1-6])(\s[^>]*)?>/i, "生の HTML タグ"],
    [/&(amp|lt|gt|quot|nbsp|#\d{2,5});/, "文字参照がそのまま"],
    [/[�]|ã[\u0080-¿]|æ[\u0080-¿]/, "文字化け"],
    [/\{\{|\}\}|\$\{/, "差し込みの書き忘れ"],
  ];
  const seenBad = new Set();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n.nodeValue;
    if (!t || !t.trim()) continue;
    const el = n.parentElement;
    if (!el || ["SCRIPT", "STYLE", "NOSCRIPT", "CODE", "PRE", "TEXTAREA"].includes(el.tagName) || el.closest("code, pre")) continue;
    if (!vis(el) && !isClosedDetails(el)) continue;
    for (const [re, label] of BAD) {
      if (re.test(t)) {
        const key = label + snippet(t);
        if (seenBad.has(key)) continue;
        seenBad.add(key);
        out.badText.push({ label, text: snippet(t), where: path(el) });
      }
    }
  }
  // 答えの欄が「記載なし」「不明」だけ（判定カード・表のセル）。文の中の「元記事に記載なし」などは正常なので見ない（警告）
  const EMPTY_ANSWER = /^(記載なし|言及なし|情報なし|不明|未定|N\/A|TBD|TODO|—|―|-)$/i;
  const emptyCells = [...main.querySelectorAll("dd, td, th + td, li, span, p, strong, b")]
    .filter((el) => el.children.length === 0 && EMPTY_ANSWER.test((el.textContent || "").trim()) && vis(el))
    .map((el) => path(el));
  for (const where of [...new Set(emptyCells)].slice(0, 5)) out.badText.push({ label: "答えのない欄（警告）", text: "記載なし・不明など", where, warn: true });
  if (/lorem ipsum/i.test(main.innerText || "")) out.badText.push({ label: "仮の文章", text: "lorem ipsum" });
  // 「記載なし」を読者に見せない（表・料金表・判定カード・本文。空の列や行は隠し、残る空欄は空のまま。2026-11-03 オーナーのルール W11）
  const seenNoData = new Set();
  for (const el of main.querySelectorAll("td, th, dd, li, p, span, strong, b, figcaption, caption")) {
    if (el.children.length > 0 && !el.matches("td, th, dd, li")) continue;
    const t = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!/記載なし/.test(t) || !vis(el) || el.closest("code, pre")) continue;
    const key = path(el) + snippet(t);
    if (seenNoData.has(key)) continue;
    seenNoData.add(key);
    out.noData.push({ text: snippet(t), where: path(el) });
  }

  // 4. ロゴ用の書体（Dela）を決まった文言以外に使っていないか
  const allowedChars = new Set([...dela.chars]);
  for (const el of all) {
    const ff = getComputedStyle(el).fontFamily || "";
    if (!/dela/i.test(ff.split(",")[0])) continue;
    const own = [...el.childNodes].filter((c) => c.nodeType === 3).map((c) => c.nodeValue).join("").trim();
    if (!own || !vis(el)) continue;
    const missing = [...own].filter((ch) => ch.charCodeAt(0) > 0x7e && !allowedChars.has(ch) && !/\s/.test(ch));
    const okText = dela.texts.some((t) => own.includes(t)) || /^[\x20-\x7e]+$/.test(own);
    if (missing.length || !okText) out.fontLeaks.push({ text: snippet(own), where: path(el), missing: [...new Set(missing)].join("") });
  }

  // 5. 同じ中身の区切りが 2 回続く（同じ表を 2 回など）
  const blocks = [...main.querySelectorAll("section, table, figure, aside, ul, ol, .prose > div, article > div")];
  const norm = (el) => (el.innerText || "").replace(/\s+/g, " ").trim();
  for (const el of blocks) {
    if (!vis(el)) continue;
    let prev = el.previousElementSibling;
    while (prev && !vis(prev)) prev = prev.previousElementSibling;
    if (!prev || prev.tagName !== el.tagName) continue;
    const a = norm(el);
    if (a.length >= 24 && a === norm(prev)) out.duplicates.push({ where: path(el), text: snippet(a) });
  }
  const tables = [...main.querySelectorAll("table")].filter(vis).map(norm).filter((t) => t.length >= 24);
  const dupTables = tables.filter((t, i) => tables.indexOf(t) !== i);
  for (const t of new Set(dupTables)) out.duplicates.push({ where: "table（離れた場所に同じ表）", text: snippet(t) });

  // 6. 提携リンクの枠に PR／広告 の表示があるか
  const LABEL = /(^|[\s　・|（(])PR([\s　・|）)]|$)|広告|プロモーション|スポンサー/;
  const pageDisclosed = /本ページはプロモーションを含みます|プロモーションを含みます/.test(document.body.innerText);
  const checkedBoxes = new Set();
  for (const a of document.querySelectorAll('a[rel~="sponsored"]')) {
    if (!vis(a)) continue;
    let box = a, found = false;
    for (let i = 0; i < 7 && box && box !== document.body; i++, box = box.parentElement) {
      if (LABEL.test(box.innerText || "") || LABEL.test(box.getAttribute("aria-label") || "")) { found = true; break; }
      if (["MAIN", "ARTICLE"].includes(box.tagName)) break;
    }
    const key = path(a.parentElement || a);
    if (!found && !checkedBoxes.has(key)) {
      checkedBoxes.add(key);
      out.prMissing.push({ where: key, text: snippet(a.innerText || a.getAttribute("aria-label") || ""), pageDisclosed });
    }
  }

  // 6b. 同じ広告の枠・メール登録欄は1ページに1つまで（見えていない方も数える。かんたん版と通常版の両方に入れない。2026-11-03 オーナーのルール W10）
  const slotCount = new Map();
  const count = (key) => slotCount.set(key, (slotCount.get(key) || 0) + 1);
  for (const el of document.querySelectorAll("[data-aff-pos]")) count(`広告の枠 ${el.getAttribute("data-aff-pos")}`);
  for (const el of document.querySelectorAll("[data-ad-position]")) count(`AdSense の枠 ${el.getAttribute("data-ad-position")}`);
  for (const el of document.querySelectorAll("[data-newsletter-signup]")) count("メール配信の登録欄");
  for (const [key, n] of slotCount) if (n > 1) out.repeatedSlots.push({ key, n });

  // 7. 中身のない箱（見出しだけ・枠だけ）
  for (const el of main.querySelectorAll("section, aside")) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    const text = norm(el);
    const media = el.querySelector("img, svg, video, iframe, canvas, input, button, a[href]");
    if (!media && text.length < 12 && r.height > 24) out.emptyBoxes.push({ where: path(el), text: snippet(text), height: Math.round(r.height) });
    const h = el.querySelector(":scope > h2, :scope > h3, :scope > div > h2");
    if (h && text.length <= norm(h).length + 2 && !media) out.emptyBoxes.push({ where: path(el), text: `見出しだけ: ${snippet(norm(h))}` });
  }
  return out;
}
/* eslint-enable */

// ---------------------------------------------------------------------------
// 実行
// ---------------------------------------------------------------------------
function slugOf(page, vp, theme) {
  const p = page.path.replace(/^\//, "").replace(/[^a-zA-Z0-9-]+/g, "_").slice(0, 60) || "home";
  return `${p}__${vp.name}_${theme}`;
}

function withInternal(path) {
  return `${BASE}${path}${path.includes("?") ? "&" : "?"}_internal=1`;
}

async function auditOne(browser, page, vp, theme, dela) {
  const ctx = await browser.newContext({
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: vp.deviceScaleFactor,
    isMobile: vp.isMobile,
    hasTouch: vp.hasTouch,
    colorScheme: theme,
    locale: "ja-JP",
    timezoneId: "Asia/Tokyo",
    reducedMotion: "reduce",
    userAgent: vp.isMobile
      ? "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1 saisoku-ui-check"
      : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 saisoku-ui-check",
  });
  const origin = new URL(BASE).origin;
  const ownFails = [];
  await ctx.route("**/*", (route) => {
    const req = route.request();
    let u;
    try { u = new URL(req.url()); } catch { return route.continue(); }
    if (BLOCKED_HOST.test(u.hostname)) return route.abort("blockedbyclient");
    if (u.origin === origin) return route.continue({ headers: { ...req.headers(), "x-saisoku-internal": "1" } });
    return route.continue();
  });
  await ctx.addInitScript(([t]) => {
    try {
      localStorage.setItem("theme", t);
      if (t === "light") localStorage.removeItem("theme");
    } catch {}
    window.__cls = 0;
    window.__clsEntries = [];
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          if (e.hadRecentInput) continue;
          window.__cls += e.value;
          window.__clsEntries.push({ value: e.value, t: Math.round(e.startTime), nodes: (e.sources || []).map((s) => s.node && (s.node.id || s.node.className || s.node.nodeName)).filter(Boolean).slice(0, 3).map(String) });
        }
      }).observe({ type: "layout-shift", buffered: true });
    } catch {}
  }, [theme]);
  const p = await ctx.newPage();
  const consoleErrors = [];
  p.on("console", (m) => {
    if (m.type() !== "error") return;
    const text = m.text();
    if (/ERR_BLOCKED_BY_CLIENT|blockedbyclient|net::ERR_FAILED/.test(text)) return; // 止めた広告・計測
    if (page.kind === "404" && /status of 404/.test(text)) return; // 404 ページそのもの
    if (/status of 404/.test(text) && /localhost|127\.0\.0\.1/.test(BASE)) return; // ローカルの next start には /img・/_vercel が無い（読み込み失敗は下で URL ごとに数える）
    const loc = m.location()?.url || "";
    if (loc && BLOCKED_HOST.test((() => { try { return new URL(loc).hostname; } catch { return ""; } })())) return;
    consoleErrors.push(text.slice(0, 240));
  });
  p.on("pageerror", (e) => consoleErrors.push(`JS エラー: ${String(e.message || e).slice(0, 240)}`));
  p.on("response", (res) => {
    const u = res.url();
    if (!u.startsWith(origin) || res.status() < 400) return;
    if (page.kind === "404" && res.request().resourceType() === "document") { page.expected404 = true; return; }
    if (/\/(api|aff|i|push)\//.test(new URL(u).pathname) && res.request().method() !== "GET") return;
    if (new URL(u).pathname.startsWith("/_vercel/")) return; // Vercel の計測（Cloudflare では使わない。ローカルでは 404）
    ownFails.push(`${res.status()} ${u.replace(origin, "")}`.slice(0, 200));
  });

  const result = { page: page.path, label: page.label, viewport: vp.name, theme, errors: [], warnings: [], shot: null };
  try {
    // 一時的な通信の失敗で落ちないよう、開けなければ 1 回だけやり直す
    const resp = await p
      .goto(withInternal(page.path), { waitUntil: "load", timeout: 45000 })
      .catch(() => p.goto(withInternal(page.path), { waitUntil: "load", timeout: 45000 }));
    const status = resp ? resp.status() : 0;
    if (page.kind === "404" ? status !== 404 : status >= 400) result.errors.push({ check: "HTTP", detail: `状態 ${status}` });
    await p.waitForTimeout(1200);
    const clsLoad = await p.evaluate(() => ({ cls: window.__cls, entries: window.__clsEntries.slice(0, 5) }));
    // 遅延読み込みの画像を出すため、下までゆっくりスクロールして戻る
    await p.evaluate(async () => {
      const step = Math.max(400, Math.floor(window.innerHeight * 0.8));
      for (let y = 0; y < document.documentElement.scrollHeight; y += step) {
        window.scrollTo(0, y);
        await new Promise((r) => setTimeout(r, 90));
      }
      window.scrollTo(0, 0);
    });
    await p.waitForLoadState("networkidle", { timeout: 6000 }).catch(() => {});
    await p.waitForTimeout(300);
    const audit = await p.evaluate(inPageAudit, { dela, viewportWidth: vp.width, blocked: [BLOCKED_HOST.source] });

    const add = (check, list, fmt, warn = false) => {
      for (const item of list.slice(0, 8)) (warn || item.warn ? result.warnings : result.errors).push({ check, detail: fmt(item) });
    };
    add("読めない画像", audit.brokenImages, (i) => `${i.src}（${i.where}）`);
    add("画像の差し替え", audit.fallbacks, (i) => `${i.why}: ${i.src}（${i.where}）`, true);
    add("はみ出し", audit.overflow, (i) => `${i.detail} ${i.where || ""} ${i.text ? `「${i.text}」` : ""}`.trim());
    add("おかしな文字", audit.badText, (i) => `${i.label}: 「${i.text}」${i.where ? `（${i.where}）` : ""}`);
    add("ロゴ用書体の漏れ", audit.fontLeaks, (i) => `「${i.text}」${i.missing ? ` 書体に無い字: ${i.missing}` : ""}（${i.where}）`);
    add("同じ区切りが2回", audit.duplicates, (i) => `${i.where}「${i.text}」`);
    add("同じ枠が2つ", audit.repeatedSlots, (i) => `${i.key} が ${i.n} 個（1ページに1つまで）`);
    add("「記載なし」の表示", audit.noData, (i) => `「${i.text}」（${i.where}）`);
    add("PR表示なしの提携リンク", audit.prMissing.filter((i) => !i.pageDisclosed), (i) => `${i.where}「${i.text}」`);
    add("PR表示なしの提携リンク", audit.prMissing.filter((i) => i.pageDisclosed), (i) => `${i.where}「${i.text}」（ページ上部の表記のみ）`, true);
    add("画像の縦横比・ぼやけ", audit.stretched, (i) => `${i.blurry ? "ぼやけ" : "引き伸ばし"} ${i.shown}（元 ${i.natural}）${i.src}`, true);
    add("代替テキストなし", audit.missingAlt, (i) => `${i.src}（${i.where}）`, true);
    add("空の箱", audit.emptyBoxes, (i) => `${i.where}「${i.text}」`, true);
    for (const e of [...new Set(consoleErrors)].slice(0, 6)) result.errors.push({ check: "コンソールのエラー", detail: e });
    for (const e of [...new Set(ownFails)].slice(0, 6)) result.errors.push({ check: "読み込み失敗", detail: e });
    if (clsLoad.cls > CLS_LIMIT) {
      result.errors.push({ check: "レイアウトのずれ", detail: `CLS ${clsLoad.cls.toFixed(3)} > ${CLS_LIMIT} ${JSON.stringify(clsLoad.entries.map((e) => [Number(e.value.toFixed(3)), e.t, e.nodes.join("|")]))}` });
    } else if (clsLoad.cls > 0.05) {
      result.warnings.push({ check: "レイアウトのずれ", detail: `CLS ${clsLoad.cls.toFixed(3)}` });
    }
    result.cls = Number(clsLoad.cls.toFixed(4));

    // 動くかどうか（スマホのライトだけで十分。同じ部品なので）
    if (vp.name === "mobile" && theme === "light") await interactions(p, page, result);
    if (vp.name === "desktop" && theme === "light" && page.kind === "home") await interactions(p, page, result);

    if (SHOTS) {
      // 成果物が大きくなりすぎない（1回 240MB になった）よう JPEG にし、ライトはページの上から最大 6000px、
      // ダークは最初の1画面だけ撮る。崩れが見つかったページは長さの上限まで撮る。
      // Chrome は 16384px（端末の画素）より長い画像を撮ると中身が繰り返されるので、それより短く切る
      const file = join(OUT, "shots", `${slugOf(page, vp, theme)}.jpg`);
      const h = await p.evaluate(() => document.documentElement.scrollHeight);
      const cap = result.errors.length ? Math.floor(16000 / vp.deviceScaleFactor) : theme === "dark" ? vp.height : 6000;
      await p
        .screenshot({ path: file, type: "jpeg", quality: 60, fullPage: true, clip: { x: 0, y: 0, width: vp.width, height: Math.min(h, cap) }, timeout: 30000 })
        .catch(async () => p.screenshot({ path: file, type: "jpeg", quality: 60, timeout: 15000 }));
      result.shot = file;
    }
    // 広告（PR）枠の見出しと商品名。記事との関係を人が見直せるように残す（押さない・取りに行かない）
    if (vp.name === "mobile" && theme === "light") {
      result.offers = await p.evaluate(() =>
        [...document.querySelectorAll('aside[aria-label*="PR"], [data-aff-pos]')]
          .filter((el) => !el.parentElement?.closest('aside[aria-label*="PR"], [data-aff-pos]'))
          .map((el) => (el.innerText || "").replace(/\s+/g, " ").trim().slice(0, 140))
          .filter(Boolean)
          .slice(0, 6),
      );
    }
    result.links = await p.evaluate((o) =>
      [...document.querySelectorAll("a[href]")]
        .filter((a) => !/sponsored/.test(a.rel))
        .map((a) => a.href)
        .filter((h) => h.startsWith(o) && !h.includes("#")),
    origin);
  } catch (e) {
    result.errors.push({ check: "読み込み", detail: String(e.message || e).split("\n")[0].slice(0, 200) });
  } finally {
    await ctx.close();
  }
  return result;
}

async function interactions(p, page, result) {
  const fail = (check, detail) => result.errors.push({ check, detail });
  result.ran = result.ran || [];
  const ran = (name) => result.ran.push(name);
  try {
    // テーマ切替
    const toggle = p.locator('button[aria-label*="モードに切り替え"]').first();
    if (await toggle.isVisible().catch(() => false)) {
      const before = await p.evaluate(() => document.documentElement.getAttribute("data-theme") || "light");
      await toggle.click();
      await p.waitForTimeout(150);
      const after = await p.evaluate(() => document.documentElement.getAttribute("data-theme") || "light");
      if (before === after) fail("ボタンが動かない", "テーマ切替で見た目が変わらない");
      await toggle.click();
      ran("テーマ切替");
    }
    if (page.kind === "home") {
      const ring = p.locator(".sr-ring").first();
      if (await ring.count()) {
        await ring.click();
        const dlg = p.getByRole("dialog");
        await dlg.first().waitFor({ timeout: 8000 }).catch(() => fail("ストーリー", "押しても開かない"));
        await p.waitForTimeout(600);
        const imgs = await p.evaluate(() => [...document.querySelectorAll('[role="dialog"] img')].filter((i) => i.complete && i.naturalWidth === 0).map((i) => i.src.slice(0, 120)));
        for (const s of imgs) fail("読めない画像", `ストーリーの中: ${s}`);
        await p.getByRole("button", { name: "閉じる" }).first().click().catch(() => fail("ストーリー", "閉じるボタンが押せない"));
        await p.waitForTimeout(300);
        if (await p.getByRole("dialog").count()) fail("ストーリー", "閉じても残る");
        const overflow = await p.evaluate(() => document.body.style.overflow);
        if (overflow === "hidden") fail("ストーリー", "閉じたあともページがスクロールできない");
        ran("ストーリーの開閉");
      } else {
        result.warnings.push({ check: "ストーリー", detail: "ストーリーの丸が見つからない" });
      }
    }
    if (page.kind === "article") {
      const play = p.locator('button[aria-label$="を再生する"]').first();
      if (await play.count()) {
        await play.scrollIntoViewIfNeeded();
        await play.click();
        const ok = await p.locator("figure video").first().waitFor({ timeout: 5000 }).then(() => true).catch(() => false);
        if (!ok) fail("動画", "再生ボタンを押しても動画が出ない");
        else {
          const err = await p.evaluate(() => new Promise((r) => { const v = document.querySelector("figure video"); if (v.error) return r(v.error.code); v.addEventListener("error", () => r(v.error && v.error.code)); setTimeout(() => r(0), 3000); }));
          if (err) fail("動画", `動画が読めない（エラー ${err}）`);
        }
        ran("動画の再生ボタン");
      }
      // 共有ボタン（押さずに行き先だけ確かめる）
      const share = await p.evaluate(() =>
        [...document.querySelectorAll('a[href*="twitter.com/intent"], a[href*="x.com/intent"], a[href*="line.me"], a[href*="facebook.com/sharer"], a[href*="threads.net"], a[href*="bsky.app/intent"], a[href*="b.hatena"]')].map((a) => a.href),
      );
      if (!share.length && !(await p.locator('button[aria-label*="共有"], button:has-text("共有"), button:has-text("コピー")').count())) {
        result.warnings.push({ check: "共有ボタン", detail: "共有ボタンが見つからない" });
      }
      if (share.length) ran(`共有ボタン ${share.length} 個`);
      for (const h of share) {
        if (/undefined|null|NaN|%22%22|url=$|text=$/.test(h)) fail("共有ボタン", `行き先がおかしい: ${h.slice(0, 160)}`);
      }
      const copy = p.locator('button:has-text("リンクをコピー"), button[aria-label*="コピー"]').first();
      if (await copy.count()) {
        await p.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(BASE).origin }).catch(() => {});
        await copy.scrollIntoViewIfNeeded().catch(() => {});
        await copy.click().catch(() => fail("共有ボタン", "コピーのボタンが押せない"));
      }
    }
    if (page.kind === "tool") {
      const inputs = p.locator('main input[type="number"], main input[inputmode="numeric"], main input[inputmode="decimal"], main input[type="text"]');
      const selects = p.locator("main select");
      const n = await inputs.count();
      const ns = await selects.count();
      if (!n && !ns) fail("計算ツール", "入力欄が見つからない");
      else {
        const box = p.locator("main");
        const before = await box.innerText();
        if (n) {
          await inputs.first().fill("12345");
          await inputs.first().blur();
        } else {
          const values = await selects.first().evaluate((s) => [...s.options].map((o) => o.value));
          const cur = await selects.first().inputValue();
          await selects.first().selectOption(values.find((v) => v !== cur) || cur);
        }
        await p.waitForTimeout(300);
        const after = await box.innerText();
        if (before === after) fail("計算ツール", "数字を入れても結果が変わらない");
        if (/NaN|undefined|Infinity/.test(after)) fail("計算ツール", "結果に NaN/undefined/Infinity が出る");
        ran("計算ツールの入力");
      }
    }
    if (page.kind === "search") {
      const count = await p.locator("main a[href^='/articles/'], main a[href*='/articles/']").count();
      if (!count) {
        // 当たりが無いときの案内が出ていれば正常（題名・要約・タグだけを探すので、本文にしか無い言葉は出ない）
        const msg = await p.locator("main").innerText();
        if (!/見つかりませんでした/.test(msg)) fail("検索", "結果も「見つかりませんでした」も出ない");
        else result.warnings.push({ check: "検索", detail: "当たり 0 本（案内は表示）" });
      }
      ran("検索");
    }
  } catch (e) {
    result.warnings.push({ check: "動作の確認", detail: String(e.message || e).split("\n")[0].slice(0, 160) });
  }
}

/** PNG / JPEG / WebP の大きさを読む（画像を開かずにヘッダーだけ） */
function imageSize(buf) {
  if (buf.length > 24 && buf.readUInt32BE(0) === 0x89504e47) return { type: "png", w: buf.readUInt32BE(16), h: buf.readUInt32BE(20) };
  if (buf.length > 30 && buf.toString("ascii", 0, 4) === "RIFF" && buf.toString("ascii", 8, 12) === "WEBP") {
    const chunk = buf.toString("ascii", 12, 16);
    if (chunk === "VP8X") return { type: "webp", w: 1 + buf.readUIntLE(24, 3), h: 1 + buf.readUIntLE(27, 3) };
    if (chunk === "VP8 ") return { type: "webp", w: buf.readUInt16LE(26) & 0x3fff, h: buf.readUInt16LE(28) & 0x3fff };
    if (chunk === "VP8L") { const b = buf.readUInt32LE(21); return { type: "webp", w: (b & 0x3fff) + 1, h: ((b >> 14) & 0x3fff) + 1 }; }
  }
  if (buf[0] === 0xff && buf[1] === 0xd8) {
    let i = 2;
    while (i < buf.length) {
      if (buf[i] !== 0xff) { i++; continue; }
      const m = buf[i + 1];
      if (m >= 0xc0 && m <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(m)) return { type: "jpeg", h: buf.readUInt16BE(i + 5), w: buf.readUInt16BE(i + 7) };
      i += 2 + buf.readUInt16BE(i + 2);
    }
  }
  return null;
}

async function checkImagesAndFeed(pages, report) {
  const slugs = pages.filter((p) => p.kind === "article").slice(0, 5).map((p) => p.article?.slug || decodeURIComponent(p.path.replace(/^\/articles\//, "").split(/[?#]/)[0]));
  // /card は lib/card-designs.tsx の CARD_SIZES（いまは写真だけの p1 = 1200×675）。描けないと /cover に転送される（エラーとして数える）
  const expect = { card: null, cover: [1200, 630] };
  for (const slug of slugs) {
    for (const kind of ["card", "cover"]) {
      const url = `${BASE}/${kind}/${slug}?_internal=1`;
      try {
        const res = await fetch(url, { headers: { "X-Saisoku-Internal": "1", "User-Agent": "saisoku-ui-check" } });
        const buf = Buffer.from(await res.arrayBuffer());
        const size = imageSize(buf);
        const item = { page: `/${kind}/${slug}`, label: `${kind} 画像`, viewport: "-", theme: "-", errors: [], warnings: [] };
        // 描けずに転送されたら崩れとして数える（2026-11-03 の点検 E。Workers で sharp の代役が同期で例外を投げ、実行時に描くカードが全部転送されていた）
        if (res.redirected) item.errors.push({ check: "画像", detail: `${kind} が描けず ${new URL(res.url).pathname} に転送された（SNS の画像がカバーに変わる）` });
        if (res.status !== 200 || !/^image\//.test(res.headers.get("content-type") || "")) item.errors.push({ check: "画像", detail: `状態 ${res.status} ${res.headers.get("content-type")}` });
        else if (!size) item.errors.push({ check: "画像", detail: "画像として読めない" });
        else {
          item.size = `${size.w}x${size.h}`;
          const want = expect[kind];
          if (want && (size.w !== want[0] || size.h !== want[1])) item.errors.push({ check: "画像の大きさ", detail: `${size.w}x${size.h}（期待 ${want.join("x")}）` });
          if (kind === "card" && !res.redirected && Math.abs(size.w / size.h - 16 / 9) > 0.02) item.errors.push({ check: "画像の大きさ", detail: `${size.w}x${size.h}（16:9 ではない）` });
          if (size.w < 200 || size.h < 200) item.errors.push({ check: "画像の大きさ", detail: `${size.w}x${size.h} は小さすぎる` });
          if (buf.length < 4000) item.warnings.push({ check: "画像", detail: `${buf.length} バイトしかない（真っ白・真っ黒の可能性）` });
        }
        report.push(item);
      } catch (e) {
        report.push({ page: `/${kind}/${slug}`, label: `${kind} 画像`, viewport: "-", theme: "-", errors: [{ check: "画像", detail: String(e) }], warnings: [] });
      }
    }
  }
  // RSS
  const feed = await fetchText(`${BASE}/feed.xml?_internal=1`);
  const item = { page: "/feed.xml", label: "RSS", viewport: "-", theme: "-", errors: [], warnings: [] };
  if (feed.status !== 200) item.errors.push({ check: "RSS", detail: `状態 ${feed.status}` });
  else {
    if (!/xml/.test(feed.type)) item.warnings.push({ check: "RSS", detail: `content-type が ${feed.type}` });
    if (!/^\s*<\?xml/.test(feed.text) || !/<item>/.test(feed.text)) item.errors.push({ check: "RSS", detail: "XML の形になっていない・記事がない" });
    const titles = [...feed.text.matchAll(/<title>([\s\S]*?)<\/title>/g)].map((m) => m[1]);
    for (const t of titles) if (/undefined|NaN|\bnull\b|\*\*/.test(t)) item.errors.push({ check: "RSS", detail: `題名がおかしい: ${t.slice(0, 80)}` });
    const bad = feed.text.match(/&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);)/i);
    if (bad) item.errors.push({ check: "RSS", detail: "エスケープしていない & がある（XML として壊れる）" });
    const dates = [...feed.text.matchAll(/<pubDate>([^<]+)<\/pubDate>/g)].map((m) => m[1]);
    for (const d of dates.slice(0, 20)) if (Number.isNaN(Date.parse(d))) item.errors.push({ check: "RSS", detail: `日付が読めない: ${d}` });
  }
  report.push(item);
}

async function checkLinks(results, report) {
  const seen = new Set();
  const links = [];
  for (const r of results) for (const l of r.links || []) {
    const u = l.replace(/[?&]_internal=1/, "");
    if (seen.has(u)) continue;
    seen.add(u);
    links.push(u);
  }
  // 種類が偏らないよう、ページごとに順番に拾う
  links.sort(() => 0.5 - Math.random());
  const sample = links.slice(0, LINK_LIMIT);
  const broken = [];
  const flaky = [];
  let i = 0;
  await Promise.all(Array.from({ length: 8 }, async () => {
    while (i < sample.length) {
      const u = sample[i++];
      // 通信の一時的な失敗（fetch failed）は 2 回までやり直し、それでも駄目なら注意にとどめる（404 などは失敗）
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const res = await fetch(u + (u.includes("?") ? "&" : "?") + "_internal=1", { method: "GET", headers: { "X-Saisoku-Internal": "1", "User-Agent": "saisoku-ui-check" }, redirect: "follow", signal: AbortSignal.timeout(20000) });
          if (res.status >= 500 && attempt < 2) { await res.body?.cancel().catch(() => {}); await new Promise((r) => setTimeout(r, 1500)); continue; }
          if (res.status >= 400) broken.push(`${res.status} ${u}`);
          await res.body?.cancel().catch(() => {});
          break;
        } catch (e) {
          if (attempt < 2) { await new Promise((r) => setTimeout(r, 1500)); continue; }
          flaky.push(`通信エラー ${u} ${String(e).slice(0, 60)}`);
        }
      }
    }
  }));
  report.push({ page: "(内部リンク)", label: `内部リンク ${sample.length} 本`, viewport: "-", theme: "-", errors: broken.map((b) => ({ check: "リンク切れ", detail: b })), warnings: flaky.map((b) => ({ check: "リンク", detail: b })) });
}

async function main() {
  if (!CHROME) throw new Error("Chrome が見つかりません（CHROME_PATH を指定してください）");
  const { chromium } = await import("playwright-core");
  mkdirSync(join(OUT, "shots"), { recursive: true });
  const dela = delaAllowed();
  const pages = await buildPages();
  const jobs = [];
  for (const page of pages) for (const vp of VIEWPORTS) for (const theme of THEMES) jobs.push({ page, vp, theme });
  console.log(`${pages.length} ページ × ${VIEWPORTS.length} 画面幅 × ${THEMES.length} テーマ = ${jobs.length} 回（${BASE}）`);
  const started = Date.now();
  const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ["--no-first-run", "--hide-scrollbars"] });
  const results = [];
  let next = 0;
  await Promise.all(Array.from({ length: CONCURRENCY }, async () => {
    while (next < jobs.length) {
      const { page, vp, theme } = jobs[next++];
      const r = await auditOne(browser, page, vp, theme, dela);
      results.push(r);
      const mark = r.errors.length ? `✗ ${r.errors.length}` : "✓";
      console.log(`${mark} ${page.path} ${vp.name}/${theme}${r.errors.length ? "  " + r.errors.slice(0, 2).map((e) => `${e.check}: ${e.detail.slice(0, 100)}`).join(" / ") : ""}`);
    }
  }));
  await browser.close();
  const extra = [];
  await checkImagesAndFeed(pages, extra);
  await checkLinks(results, extra);
  const all = [...results.sort((a, b) => (a.page + a.viewport + a.theme).localeCompare(b.page + b.viewport + b.theme)), ...extra];
  for (const r of all) delete r.links;
  for (const r of all) if (r.offers && !r.offers.length) delete r.offers;
  const errors = all.reduce((n, r) => n + r.errors.length, 0);
  const warnings = all.reduce((n, r) => n + r.warnings.length, 0);
  const secs = Math.round((Date.now() - started) / 1000);
  writeFileSync(join(OUT, "report.json"), JSON.stringify({ base: BASE, set: SET, at: new Date().toISOString(), seconds: secs, errors, warnings, results: all }, null, 2));
  // issue・まとめ用の Markdown（同じ崩れはページをまたいでまとめる）
  const lines = [`# 画面の崩れの点検（${new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" })}）`, "", `対象: ${BASE}（${pages.length} ページ × スマホ/PC × ライト/ダーク、${secs} 秒）`, "", `エラー ${errors} 件・注意 ${warnings} 件`, ""];
  const groups = new Map();
  for (const r of all) for (const e of r.errors) {
    const key = `${e.check}|${e.detail}`;
    if (!groups.has(key)) groups.set(key, { ...e, where: [] });
    groups.get(key).where.push(`${r.page} ${r.viewport}/${r.theme}`);
  }
  if (groups.size) {
    lines.push("| 種類 | 内容 | ページ（画面幅/テーマ） |", "|---|---|---|");
    for (const g of groups.values()) {
      const where = g.where.length > 4 ? `${g.where.slice(0, 4).join("<br>")}<br>ほか ${g.where.length - 4} 件` : g.where.join("<br>");
      lines.push(`| ${g.check} | ${g.detail.replace(/\|/g, "／").slice(0, 220)} | ${where} |`);
    }
  }
  const wgroups = new Map();
  for (const r of all) for (const e of r.warnings) {
    const key = `${e.check}|${e.detail}`;
    if (!wgroups.has(key)) wgroups.set(key, { ...e, where: [] });
    wgroups.get(key).where.push(`${r.page} ${r.viewport}/${r.theme}`);
  }
  if (wgroups.size) {
    lines.push("", "<details><summary>注意（失敗にはしない）</summary>", "", "| 種類 | 内容 | ページ |", "|---|---|---|");
    for (const g of [...wgroups.values()].slice(0, 60)) lines.push(`| ${g.check} | ${g.detail.replace(/\|/g, "／").slice(0, 200)} | ${g.where.slice(0, 3).join("<br>")}${g.where.length > 3 ? ` ほか ${g.where.length - 3}` : ""} |`);
    lines.push("", "</details>");
  }
  const ranRows = all.filter((r) => r.ran?.length).map((r) => `- ${r.page}（${r.viewport}）: ${r.ran.join("・")}`);
  if (ranRows.length) lines.push("", "<details><summary>動かして確かめたもの</summary>", "", ...ranRows, "", "</details>");
  const offerRows = all.filter((r) => r.offers?.length).map((r) => `- ${r.page}: ${r.offers.map((o) => `「${o.replace(/\|/g, "／").slice(0, 90)}」`).join(" ")}`);
  if (offerRows.length) lines.push("", "<details><summary>広告（PR）枠の中身（記事と合っているかを目で見る用）</summary>", "", ...offerRows, "", "</details>");
  writeFileSync(join(OUT, "report.md"), lines.join("\n") + "\n");
  console.log(`\nエラー ${errors} 件・注意 ${warnings} 件（${secs} 秒）。詳しくは ${join(OUT, "report.md")}`);
  process.exit(errors ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
