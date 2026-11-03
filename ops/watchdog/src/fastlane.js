/**
 * 速報レーン（2分ごと）: 主要企業の公式ニュースルームだけを見て、新しい重要な発表を見つけたら
 * すぐ記事作り（auto_run.yml の mode=web + fast_url）を起動する。
 *
 * - 判定ルールは scripts/fast_lane_rules.json（Python 側 scripts/fast_lane.py と同じ判定。npm test で突き合わせる）
 * - フィードは ETag / Last-Modified で条件付き取得。見た記事の ID は KV（キー "state"）に残す
 * - 初めて読む発信元は「いまある記事は全部既読」にするだけ（デプロイ直後に昔の記事を流さない）
 * - 起動した記事は、パイプラインが state/fast_lane.json に結果を書くまで KV に残し、
 *   取り消し・失敗・行方不明なら起動し直す（最大 max_dispatches 回）
 * - GitHub は同じ concurrency group の待機を1つしか持てない（新しい方が古い待機を取り消す）ので、
 *   速報レーンの実行が待機中なら新しく起動しない（次の回に回す）
 * - レーダー（radar.js）が KV の "radar" の outbox に置いた話題も、ここで queue に取り込んで同じ流れで起動する
 * - 1日の新規起動は FAST_DAILY_MAX（既定 daily_max・速報レーン＋レーダー合計）まで。これは暴走よけの安全上限で、
 *   通常の記事の上限とは別。超えた・諦めた話題は黙って落とさず GitHub の Issue（ラベル missed-news）で知らせる
 * - FAST_DRY_RUN=1 なら起動せずログだけ
 */

const UA = "Mozilla/5.0 (compatible; SaisokuFastLane/1.0; +https://ai-tech-express.jp)";
export const RUN_PREFIX = "速報レーン";
const MAX_SEEN = 400;
// 1つのフィードで見るのは新しい順にこの件数まで（OpenAI の RSS は1000件超・800KB 近くある）
const MAX_ITEMS = 60;
const MAX_BLOCKS = 200;
const KEEP_DONE_HOURS = 72;
const TERMINAL = new Set(["created", "updated", "duplicate", "skipped", "failed", "capped"]);
const WAITING = new Set(["queued", "pending", "waiting", "requested"]);

// --------------------------------------------------------------------------- //
// ルール
// --------------------------------------------------------------------------- //

/** scripts/sources_registry.json（追加の発信元）を rules の sources に足す。同じ slug は rules 側を優先 */
export function mergeRegistry(rules, registry) {
  if (!registry?.sources?.length) return rules;
  const have = new Set(rules.sources.map((s) => s.slug));
  const extra = registry.sources.filter((s) => !have.has(s.slug)).map((s) => ({ ...s, registry: true }));
  const { sources: _omit, ...conf } = registry;
  return { ...rules, sources: [...rules.sources, ...extra], registry: conf };
}

export const WAIT_REASON = "話題待ち（強い語なし）";
const escapeRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** 会社（entity）ごとの「まとめサイトに名前が出たか」の正規化前の正規表現（大文字小文字を区別する）。tier B・C の追加の発信元だけ */
export function buzzPatterns(rules) {
  const out = {};
  for (const s of rules.sources || []) {
    if (!s.registry || !s.entity || !["B", "C"].includes(s.tier)) continue;
    const re = s.buzz_re || `(?<![A-Za-z])${escapeRe(s.company || s.entity)}(?![A-Za-z])`;
    (out[s.entity] ||= new Set()).add(re);
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, new RegExp([...v].join("|"))]));
}

export function compile(rules) {
  const re = (s) => new RegExp(s, "i");
  const reg = rules.registry || {};
  return {
    ...rules,
    sources: rules.sources.filter((s) => !s.disabled),
    bySlug: Object.fromEntries(rules.sources.map((s) => [s.slug, s])),
    model: re(rules.model_re),
    head: re(rules.head_re),
    launch: re(rules.launch_re),
    exclude: re(rules.exclude_re),
    ai: re(rules.ai_re),
    // ジャンル（新製品・ゲーム・ビジネス）ごとの判定
    genreRe: Object.fromEntries(
      Object.entries(rules.genres || {}).filter(([k]) => !k.startsWith("_")).map(([k, g]) => [
        k, { major: re(g.major_re), exclude: re(g.exclude_re), own: Boolean(g.own_exclude), reason: g.reason || "新製品・ゲームの発表" },
      ]),
    ),
    // 追加の発信元（tier B・C）の強い語・新モデル、発信元ごとの除外語・必須語
    strong: reg.strong_re ? re(reg.strong_re) : null,
    regModel: reg.model_re ? re(reg.model_re) : null,
    srcRe: Object.fromEntries(
      rules.sources.filter((s) => s.exclude_re || s.include_re).map((s) => [
        s.slug, { exclude: s.exclude_re ? re(s.exclude_re) : null, include: s.include_re ? re(s.include_re) : null },
      ]),
    ),
  };
}

/** 発信元のジャンル（無ければ ai。レーダーの話題も ai として数える） */
export const genreOf = (R, slug) => R.bySlug[slug]?.genre || "ai";

/** tier B・C（強い語か話題が要る発信元） */
export const lazyTier = (src) => src?.tier === "B" || src?.tier === "C";

/** この回に見に行くか。tier A と元からの発信元は毎回、B・C は間隔ごと（発信元ごとに回をずらして負荷を散らす） */
export function due(src, now, R) {
  const every = Number(R.registry?.tiers?.[src.tier] || 2);
  const slots = Math.max(1, Math.round(every / 2));
  if (slots <= 1) return true;
  const tick = now.getUTCHours() * 30 + Math.floor(now.getUTCMinutes() / 2);
  let h = 0;
  for (const c of src.slug) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return tick % slots === h % slots;
}

/** 速報レーンに乗せるか。理由の文言は scripts/fast_lane.py の relevant() と同じ */
export function relevant(title, slug, R) {
  const src = R.bySlug[slug];
  if (!src) return { ok: false, reason: "対象外の発信元" };
  const t = String(title || "").trim();
  const g = src.genre && R.genreRe[src.genre];
  if (!g?.own) {
    const ex = t.match(R.exclude);
    if (ex) return { ok: false, reason: `除外語（${ex[0]}）` };
  }
  const own = R.srcRe[slug];
  const ox = own?.exclude && t.match(own.exclude);
  if (ox) return { ok: false, reason: `除外語（${ox[0]}）` };
  if (own?.include && !own.include.test(t)) return { ok: false, reason: "必須の語がない" };
  if (g) {
    // 新製品・ゲーム・ビジネスの公式: 細かいお知らせを除き、大きな発表の語があるものだけ
    const gx = t.match(g.exclude);
    if (gx) return { ok: false, reason: `除外語（${gx[0]}）` };
    if (R.model.test(t) && (R.launch.test(t) || R.head.test(t))) return { ok: true, reason: "新モデル・新製品" };
    if (g.major.test(t)) return { ok: true, reason: g.reason };
    return { ok: false, reason: "大きな発表の語がない" };
  }
  const model = R.model.test(t);
  if (src.ai_only && !model && !R.ai.test(t)) return { ok: false, reason: "AI の話題ではない" };
  const launch = R.launch.test(t);
  if (lazyTier(src)) {
    // tier B・C: 強い語・新モデルだけすぐ。発表の語だけなら、まとめサイトで話題になるのを待つ（fastLane の pending）
    if (R.strong?.test(t)) return { ok: true, reason: "大きな発表" };
    if (R.regModel?.test(t) && (launch || R.head.test(t))) return { ok: true, reason: "新モデル・新製品" };
    if (launch) return { ok: false, reason: WAIT_REASON };
    return { ok: false, reason: "発表の語がない" };
  }
  if (model && (launch || R.head.test(t))) return { ok: true, reason: "新モデル・新製品" };
  // 対象は各社の公式ニュースルームだけなので、発表の語があれば自社（か提携先と共同）の発表とみなす
  if (launch) return { ok: true, reason: "公式の発表" };
  return { ok: false, reason: "発表の語がない" };
}

/** 見出しの語（話題の突き合わせ用）: 英数字3文字以上・カタカナ3文字以上・漢字2文字以上。stop は除く */
export function tokens(text, stop = new Set()) {
  const out = new Set();
  for (const w of String(text || "").toLowerCase().match(/[a-z0-9][a-z0-9.+-]*[a-z0-9]|[ァ-ヶー]{3,}|[一-龠々]{2,}/g) || []) {
    if ((/^[a-z0-9]/.test(w) && w.length < 3) || stop.has(w) || /^\d+$/.test(w)) continue;
    out.add(w);
  }
  return out;
}

/** 話題の突き合わせで数えない語（レーダーの stop_words ＋ sources_registry.json の buzz_stop） */
export const buzzStop = (R) => new Set([...(R.radar?.stop_words || []), ...(R.registry?.buzz_stop || [])]);

/** 2つの見出しがほぼ同じか（語の重なり。短い方の7割以上が共通） */
export function similar(a, b, stop) {
  const x = tokens(a, stop);
  const y = tokens(b, stop);
  if (x.size < 2 || y.size < 2) return false;
  const shared = [...x].filter((w) => y.has(w)).length;
  return shared / Math.min(x.size, y.size) >= 0.7;
}

/** 話題待ちの見出しが、まとめサイトの見出し（buzz[entity]）と同じ話題か。会社名の語は数えない */
export function buzzMatch(title, list, nameRe, stop) {
  const name = nameRe ? new RegExp(nameRe.source, "i") : null;
  const mine = new Set([...tokens(title, stop)].filter((w) => !name?.test(w)));
  // 短い見出し（語が2つまで）は1語、それより長ければ2語の重なりが要る（「Gemini」だけの重なりで別の話題を拾わない）
  const need = mine.size <= 2 ? 1 : 2;
  for (const b of list || []) {
    const shared = [...tokens(b.t, stop)].filter((w) => mine.has(w));
    if (shared.length >= need) return { ...b, shared };
  }
  return null;
}

// --------------------------------------------------------------------------- //
// 解析（標準のパーサーが無いので正規表現で。RSS 2.0 / Atom / RSS 1.0 と、一覧ページの HTML）
// --------------------------------------------------------------------------- //

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };

export function decode(s) {
  return String(s).replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === "#") {
      const code = e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : m;
    }
    return ENTITIES[e.toLowerCase()] ?? m;
  });
}

/** 二重に書かれた実体参照（&amp;#8211; など）も戻す。文字として残った「&#8211;」が見出しに出ないように、最大3回まで */
export function decodeText(s) {
  let out = String(s);
  for (let i = 0; i < 3; i += 1) {
    const next = decode(out);
    if (next === out) break;
    out = next;
  }
  return out;
}

/** CDATA を外し、タグを除いてから実体参照を戻す（Python の feeds.strip_html と同じ順） */
export function textOf(s) {
  const raw = String(s || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1");
  return decodeText(raw.replace(/<[^>]+>/g, " ")).replace(/\s+/g, " ").trim();
}

function tag(block, name) {
  // 自己終了タグ（<link .../>）は拾わない
  const m = block.match(new RegExp(`<${name}(?:\\s[^>]*)?(?<!/)>([\\s\\S]*?)</${name}>`, "i"));
  return m ? m[1] : "";
}

function firstBlocks(xml, name, limit) {
  const re = new RegExp(`<${name}\\b[\\s\\S]*?</${name}>`, "gi");
  const out = [];
  for (let m = re.exec(xml); m && out.length < limit; m = re.exec(xml)) out.push(m[0]);
  return out;
}

/** 新しい順に MAX_ITEMS 件（日付が全部あれば日付順、無ければ載っている順） */
export function newest(items) {
  const sorted = items.every((i) => i.publishedAt) ? [...items].sort((a, b) => b.publishedAt.localeCompare(a.publishedAt)) : items;
  return sorted.slice(0, MAX_ITEMS);
}

/** base があれば相対リンク（Sakana AI の Atom など）を絶対 URL にする */
export function parseFeed(xml, base = "") {
  let blocks = firstBlocks(xml, "item", MAX_BLOCKS);
  if (!blocks.length) blocks = firstBlocks(xml, "entry", MAX_BLOCKS);
  const items = [];
  for (const b of blocks) {
    const title = textOf(tag(b, "title"));
    let link = textOf(tag(b, "link"));
    if (!link) {
      // Atom: rel が無いか alternate の link
      for (const m of b.matchAll(/<link\b([^>]*)>/gi)) {
        const attrs = m[1];
        const rel = (attrs.match(/\brel=["']([^"']*)["']/i) || [])[1] || "alternate";
        const href = (attrs.match(/\bhref=["']([^"']*)["']/i) || [])[1];
        if (rel === "alternate" && href) {
          link = decode(href).trim();
          break;
        }
      }
    }
    if (base && link && !/^https?:\/\//i.test(link)) {
      try {
        link = new URL(link, base).toString();
      } catch {
        // 壊れたリンクは飛ばす
      }
    }
    if (!title || !/^https?:\/\//i.test(link)) continue;
    const date = textOf(tag(b, "pubDate") || tag(b, "dc:date") || tag(b, "published") || tag(b, "updated"));
    const ts = Date.parse(date);
    items.push({ title, url: link, publishedAt: Number.isFinite(ts) ? new Date(ts).toISOString() : null });
  }
  return items;
}

/** RSS の無い一覧ページ（Anthropic /news など）: 見出し（h1〜h4）を含むリンクだけを記事とみなす */
export function parseHtmlList(html, src) {
  const base = new URL(src.url);
  const linkRe = new RegExp(src.link_re || ".", "i");
  const seen = new Map();
  const items = [];
  for (const m of html.matchAll(/<a\b[^>]*?\bhref=["']([^"'#?]+)["'][^>]*>([\s\S]*?)<\/a>/gi)) {
    let url;
    try {
      url = new URL(decode(m[1]), base);
    } catch {
      continue;
    }
    if (url.host !== base.host || !linkRe.test(url.pathname)) continue;
    // 見出し（h1〜h4）を含むリンクだけ。title_from: "text" の発信元はリンクの文字そのもの（見出しタグの無い一覧）
    const h = m[2].match(/<h[1-4]\b[^>]*>([\s\S]*?)<\/h[1-4]>/i);
    if (!h && src.title_from !== "text") continue;
    const title = textOf(h ? h[1] : m[2]).slice(0, 200);
    const key = url.origin + url.pathname.replace(/\/+$/, "");
    if (title.length < 8) continue;
    if (seen.has(key)) {
      // 同じ記事へのリンクが何度も出る一覧（「Learn more」など）は、いちばん長い文字を見出しにする
      const it = seen.get(key);
      if (src.title_from === "text" && title.length > it.title.length) it.title = title;
      continue;
    }
    const it = { title, url: key, publishedAt: null };
    seen.set(key, it);
    items.push(it);
  }
  return items;
}

// --------------------------------------------------------------------------- //
// 記事本文（GitHub Actions からは openai.com などが 403 で読めないので、起動するときにこちらで読んで渡す）
// --------------------------------------------------------------------------- //

const TEXT_CAP = 12000;
const plainLen = (s) => textOf(s).length;

export function metaContent(html, key) {
  const k = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const m =
    html.match(new RegExp(`<meta\\b[^>]*(?:property|name)=["']${k}["'][^>]*\\bcontent=["']([^"']*)["']`, "i")) ||
    html.match(new RegExp(`<meta\\b[^>]*\\bcontent=["']([^"']*)["'][^>]*(?:property|name)=["']${k}["']`, "i"));
  return m ? decode(m[1]).trim() : "";
}

/** 簡易リーダビリティ（article → main → body の順に、本文らしい部分のテキスト） */
export function htmlToText(html) {
  const h = String(html).replace(/<(script|style|noscript|svg|template|iframe)\b[\s\S]*?<\/\1>/gi, " ");
  const inner = (name) => (h.match(new RegExp(`<${name}\\b[^>]*>([\\s\\S]*)</${name}>`, "i")) || [])[1] || "";
  let body = inner("article");
  if (plainLen(body) < 500) body = inner("main");
  if (plainLen(body) < 500) body = inner("body") || h;
  body = body
    .replace(/<(nav|footer|aside|form|button)\b[\s\S]*?<\/\1>/gi, " ")
    .replace(/<\/?(p|div|section|li|h[1-6]|br|tr|blockquote|pre|figure|figcaption|dd|dt|ul|ol|table)\b[^>]*>/gi, "\n");
  return decode(body.replace(/<[^>]+>/g, " "))
    .split("\n")
    .map((l) => l.replace(/[ \t ]+/g, " ").trim())
    .filter((l) => l.length >= 2)
    .join("\n")
    .slice(0, TEXT_CAP);
}

const BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36";

/** {text, image, published, status, via}。直接読めない・薄いときはブラウザの UA、読み取りサービス（r.jina.ai）の順に取り直す。
 *  失敗しても例外にしない（status に最後の HTTP 状態・エラーを残す） */
export async function fetchArticle(url, fetchImpl, readerKey = "") {
  const out = { text: "", image: "", published: "", title: "", status: [], via: "" };
  for (const ua of [UA, BROWSER_UA]) {
    try {
      const res = await fetchImpl(url, {
        headers: { "User-Agent": ua, Accept: "text/html,application/xhtml+xml", "Accept-Language": "en,ja;q=0.8" },
        signal: AbortSignal.timeout(12000),
      });
      out.status.push(res.status);
      if (res.ok) {
        const html = await res.text();
        const text = htmlToText(html);
        if (text.length > out.text.length) [out.text, out.via] = [text, ua === UA ? "direct" : "direct-browser"];
        out.image ||= metaContent(html, "og:image") || metaContent(html, "twitter:image");
        out.published ||= metaContent(html, "article:published_time");
        out.title ||= metaContent(html, "og:title") || textOf((html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || "");
      }
    } catch (e) {
      out.status.push(String(e).slice(0, 40));
    }
    if (out.text.length >= 600) return out;
  }
  try {
    // 鍵（secret JINA_API_KEY）があれば使う。無いと Cloudflare の送信元は回数制限（429）にかかりやすい
    const headers = { Accept: "text/plain", ...(readerKey ? { Authorization: `Bearer ${readerKey}` } : {}) };
    const res = await fetchImpl(`https://r.jina.ai/${url}`, { headers, signal: AbortSignal.timeout(20000) });
    out.status.push(`reader:${res.status}`);
    if (res.ok) {
      const body = await res.text();
      const at = body.indexOf("Markdown Content:");
      const md = (at >= 0 ? body.slice(at + 17) : body)
        .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
        .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
      const text = md.split("\n").map((l) => l.trim()).filter((l) => l.length >= 2).join("\n").slice(0, TEXT_CAP);
      if (text.length > out.text.length) [out.text, out.via] = [text, "reader"];
      out.published ||= ((body.match(/^Published Time:\s*(.+)$/m) || [])[1] || "").trim();
    }
  } catch {
    // 取れなければパイプライン側の取得に任せる
  }
  return out;
}

/** Python の feeds.item_id と同じ正規化（utm_* と末尾スラッシュを無視） */
export function normalizeUrl(u) {
  const x = new URL(String(u).trim());
  const params = [...x.searchParams].filter(([k]) => !k.toLowerCase().startsWith("utm_"));
  const q = new URLSearchParams(params).toString();
  return `${x.protocol}//${x.host.toLowerCase()}${x.pathname.replace(/\/+$/, "")}${q ? `?${q}` : ""}`;
}

export async function itemId(u) {
  const data = new TextEncoder().encode(normalizeUrl(u));
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-1", data));
  return [...hash].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

/** 日本時間の日付（1日の上限の数え方） */
export function jstDate(now) {
  return new Date(now.getTime() + 9 * 3600e3).toISOString().slice(0, 10);
}

// --------------------------------------------------------------------------- //
// 取得
// --------------------------------------------------------------------------- //

async function poll(src, st, fetchImpl) {
  const headers = {
    "User-Agent": UA,
    Accept: src.type === "html" ? "text/html,application/xhtml+xml" : "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en,ja;q=0.8",
  };
  if (st?.etag) headers["If-None-Match"] = st.etag;
  if (st?.lm) headers["If-Modified-Since"] = st.lm;
  const res = await fetchImpl(src.url, { headers, signal: AbortSignal.timeout(12000), cache: "no-store" });
  if (res.status === 304) return { status: 304, items: null };
  if (!res.ok) return { status: res.status, items: null };
  const body = await res.text();
  const items = newest(src.type === "html" ? parseHtmlList(body, src) : parseFeed(body, src.url));
  return { status: res.status, items, etag: res.headers.get("etag") || "", lm: res.headers.get("last-modified") || "" };
}

/**
 * 新着の判定（KV の state.sources を書き換える）。戻り値: { fresh: [...], changed: bool }
 * - 初回は既読にするだけ / 0件（解析失敗）なら既読の記録を触らない
 */
export async function detect(srcState, src, result, now, maxSeen = MAX_SEEN) {
  if (!result.items) return { fresh: [], changed: false };
  if (result.items.length === 0) return { fresh: [], changed: false, empty: true };
  const ids = await Promise.all(result.items.map((it) => itemId(it.url)));
  const withIds = result.items.map((it, k) => ({ ...it, id: ids[k] }));
  const meta = { etag: result.etag || "", lm: result.lm || "" };
  if (!srcState) {
    return { fresh: [], changed: true, baseline: true, next: { ...meta, seen: [...new Set(ids)].slice(0, maxSeen), since: now.toISOString() } };
  }
  const seen = new Set(srcState.seen || []);
  const fresh = withIds.filter((it) => !seen.has(it.id));
  const next = { ...srcState, ...meta };
  if (fresh.length) next.seen = [...new Set([...ids, ...(srcState.seen || [])])].slice(0, maxSeen);
  const changed = fresh.length > 0 || meta.etag !== (srcState.etag || "") || meta.lm !== (srcState.lm || "");
  return { fresh, changed, next };
}

// --------------------------------------------------------------------------- //
// GitHub
// --------------------------------------------------------------------------- //

export function makeGh(env, fetchImpl = fetch) {
  return async function gh(path, init = {}) {
    const res = await fetchImpl(`https://api.github.com/repos/${env.REPO}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${env.GH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "saisoku-watchdog",
        "X-GitHub-Api-Version": "2022-11-28",
        ...(init.headers || {}),
      },
    });
    if (res.status === 404 && init.allow404) return null;
    if (!res.ok && res.status !== 204) throw new Error(`GitHub ${path} -> ${res.status} ${(await res.text()).slice(0, 200)}`);
    if (res.status === 204) return null;
    return init.raw ? res.text() : res.json();
  };
}

/** パイプラインの処理結果（state/fast_lane.json の items）。API で読めなければ公開の raw（最大5分古い）で読む */
export async function readAcks(gh, env, fetchImpl, now) {
  let text;
  try {
    text = await gh(`/contents/state/fast_lane.json?ref=main`, {
      headers: { Accept: "application/vnd.github.raw+json" },
      raw: true,
      allow404: true,
    });
  } catch {
    const res = await fetchImpl(`https://raw.githubusercontent.com/${env.REPO}/main/state/fast_lane.json?t=${now.getTime()}`, {
      cache: "no-store",
    });
    text = res.ok ? await res.text() : "";
  }
  if (!text) return {};
  try {
    return JSON.parse(text).items || {};
  } catch {
    return {};
  }
}

// --------------------------------------------------------------------------- //
// 1回分
// --------------------------------------------------------------------------- //

/** オーナーへの知らせ（GitHub の Issue。ラベル missed-news でメールが届く）。同じ key は seen に記録して1回だけ */
export function makeNotify(gh, seen, now) {
  return async (key, title, body) => {
    if (seen[key]) return false;
    await gh(`/issues`, { method: "POST", body: JSON.stringify({ title, body, labels: ["missed-news"] }) });
    seen[key] = now.toISOString();
    return true;
  };
}

/** 記事にならずに終わった登録を、見逃しチェック・手動の依頼（または諦めた・上限で止めた話題）なら起動し直してよいか */
export function reopenable(cur, e) {
  if (["new", "dispatched"].includes(cur.status) || ["created", "updated", "duplicate"].includes(cur.outcome)) return false;
  return /^(request|manual|missed-check)/.test(e.via || "") || ["gave_up", "capped", "dry"].includes(cur.status);
}

/** レーダーが outbox に置いた話題を queue に取り込む（"radar" は読むだけ。取り込んだ印は state.radarTaken） */
export function takeRadar(state, radarState, now, log) {
  let dirty = false;
  state.radarTaken ||= {};
  for (const e of Object.values(radarState?.outbox || {})) {
    if (!e?.id || state.radarTaken[e.id]) continue;
    state.radarTaken[e.id] = now.toISOString();
    dirty = true;
    const cur = state.queue[e.id];
    if (cur && !reopenable(cur, e)) {
      log.radar.push(`登録済み: ${String(e.title).slice(0, 60)}`);
      continue;
    }
    state.queue[e.id] = {
      id: e.id, url: e.url, title: e.title, source: e.source, reason: `レーダー（${e.via}）`,
      publishedAt: e.publishedAt || null, detectedAt: e.detectedAt || now.toISOString(), status: "new", dispatches: 0,
      sourceName: e.sourceName || "", secondary: Boolean(e.secondary), via: e.via || "", found: e.found || "",
    };
    log.radar.push(`取り込み: ${String(e.title).slice(0, 60)}${e.secondary ? `（報道: ${e.sourceName}）` : ""}`);
  }
  for (const [id, at] of Object.entries(state.radarTaken)) {
    if (now - Date.parse(at) > 96 * 3600e3) {
      delete state.radarTaken[id];
      dirty = true;
    }
  }
  return dirty;
}

/**
 * @param env  KV（FAST）・GH_TOKEN・REPO・WORKFLOW・FAST_DRY_RUN・FAST_DAILY_MAX
 * @param deps テスト用の差し替え { rules, fetch, gh }
 */
export async function fastLane(env, now, deps = {}) {
  const R = compile(deps.rules);
  const fetchImpl = deps.fetch || fetch;
  const gh = deps.gh || makeGh(env, fetchImpl);
  const dry = String(env.FAST_DRY_RUN || "") === "1";
  const state = (await env.FAST.get("state", "json")) || {};
  state.sources ||= {};
  state.queue ||= {};
  state.daily ||= {};
  state.notified ||= {};
  const notify = makeNotify(gh, state.notified, now);
  state.pending ||= {};
  const log = { dry, polled: 0, notModified: 0, errors: {}, baseline: [], new: [], queued: [], radar: [] };
  let dirty = false;

  // 1. 取得と新着の判定（追加の発信元の tier B・C は間隔ごと）
  const order = new Map(R.sources.map((s, k) => [s.slug, k]));
  const dueSources = R.sources.filter((src) => due(src, now, R));
  const results = await Promise.all(
    dueSources.map((src) => poll(src, state.sources[src.slug], fetchImpl).catch((e) => ({ error: String(e).slice(0, 120) }))),
  );
  const candidates = [];
  const buzzHours = Number(R.registry?.buzz_hours || 6);
  for (const [i, src] of dueSources.entries()) {
    const r = results[i];
    const k = order.get(src.slug);
    log.polled += 1;
    if (r.error || (!r.items && r.status !== 304)) {
      log.errors[src.slug] = r.error || r.status;
      continue;
    }
    if (r.status === 304) {
      log.notModified += 1;
      continue;
    }
    const d = await detect(state.sources[src.slug], src, r, now, src.registry ? Number(R.registry?.max_seen || MAX_SEEN) : MAX_SEEN);
    if (d.empty) {
      log.errors[src.slug] = "0件（解析できず）";
      continue;
    }
    if (d.changed) {
      state.sources[src.slug] = d.next;
      dirty = true;
    }
    if (d.baseline) log.baseline.push(`${src.slug}:${r.items.length}`);
    for (const it of d.fresh) {
      const rel = relevant(it.title, src.slug, R);
      const ageH = it.publishedAt ? (now - Date.parse(it.publishedAt)) / 3600e3 : 0;
      let verdict = rel.ok ? "対象" : rel.reason;
      if (rel.ok && ageH > R.max_age_hours) verdict = `古い（${Math.round(ageH)}時間前）`;
      if (rel.ok && state.queue[it.id]) verdict = "処理待ちに登録済み";
      if (rel.reason === WAIT_REASON && ageH <= R.max_age_hours && !state.queue[it.id]) {
        // tier B・C の発表の語だけの見出し: buzz_hours 時間以内にまとめサイトで話題になったら速報にする
        state.pending[it.id] = {
          id: it.id, url: it.url, title: it.title, source: src.slug, entity: src.entity, publishedAt: it.publishedAt,
          detectedAt: now.toISOString(), until: new Date(now.getTime() + buzzHours * 3600e3).toISOString(), order: k,
        };
        dirty = true;
      }
      log.new.push(`${src.slug}: ${it.title.slice(0, 70)} → ${verdict}`);
      if (verdict === "対象") candidates.push({ ...it, source: src.slug, entity: src.entity, reason: rel.reason, order: k });
    }
  }

  // 話題待ち: レーダーが KV の "buzz" に残したまとめサイトの見出しに、同じ会社・同じ話題が出たか
  if (Object.keys(state.pending).length) {
    let buzz = {};
    try {
      buzz = (await env.FAST.get("buzz", "json")) || {};
    } catch (e) {
      log.errors.buzz = String(e).slice(0, 80);
    }
    const names = buzzPatterns(R);
    const stop = buzzStop(R);
    for (const [id, p] of Object.entries(state.pending)) {
      const from = Date.parse(p.detectedAt) - buzzHours * 3600e3;
      const list = (buzz[p.entity] || []).filter((b) => Date.parse(b.at) >= from);
      const hit = state.queue[id] ? null : buzzMatch(p.title, list, names[p.entity], stop);
      if (hit) {
        candidates.push({ ...p, reason: `公式の発表＋話題（${hit.src}）`, buzz: hit.t.slice(0, 80) });
        log.new.push(`${p.source}: ${p.title.slice(0, 70)} → 話題になった（${hit.src}: ${hit.t.slice(0, 50)}）`);
      } else if (!state.queue[id] && now < Date.parse(p.until)) {
        continue;
      }
      delete state.pending[id];
      dirty = true;
    }
  }

  // 同じ回に同じ会社の発表が複数あれば1本だけ（大きな発表は Google の複数のブログに同時に載るため）。
  // 新モデル・新製品を優先し、同じならルールの並び順（本家のブログが先）
  // 同じ会社の同じ発表が別の URL で載ったもの（NVIDIA のブログとニュースルームなど）は、48時間以内の登録と見出しがほぼ同じなら見送る
  const stop = buzzStop(R);
  const recent = Object.values(state.queue).filter((e) => now - Date.parse(e.detectedAt) < 48 * 3600e3);
  const dup = (c) => recent.find((e) => (R.bySlug[e.source]?.entity || "") === c.entity && e.id !== c.id && similar(e.title, c.title, stop));
  const best = new Map();
  for (const c of candidates) {
    if ((c.dup = dup(c))) continue;
    const cur = best.get(c.entity);
    const rank = (x) => (x.reason === "新モデル・新製品" ? 0 : 1) * 1000 + x.order;
    if (!cur || rank(c) < rank(cur)) best.set(c.entity, c);
  }
  for (const c of candidates) {
    const picked = !c.dup && best.get(c.entity) === c;
    state.queue[c.id] = {
      id: c.id, url: c.url, title: c.title, source: c.source, reason: c.reason,
      publishedAt: c.publishedAt, detectedAt: now.toISOString(), genre: genreOf(R, c.source),
      status: picked ? "new" : "merged", dispatches: 0,
      // 一覧ページの文字から取った見出し（日付・筆者が混じる）は、起動するとき記事ページの題に置き換える
      ...(R.bySlug[c.source]?.page_title ? { pageTitle: true } : {}),
      ...(c.buzz ? { found: c.buzz } : {}),
    };
    dirty = true;
    log.queued.push(`${picked ? "登録" : c.dup ? `登録済みの発表と同じ（${c.dup.source}）` : "同じ会社の発表と重複（見送り）"}: ${c.title.slice(0, 60)}`);
  }

  // レーダーの話題（KV の "radar" の outbox）
  try {
    if (takeRadar(state, await env.FAST.get("radar", "json"), now, log)) dirty = true;
  } catch (e) {
    log.errors.radar = String(e).slice(0, 80);
  }

  // 2. 起動済みの記事の確認と、新規・再起動
  if (await manageQueue(env, state, now, log, R, gh, dry, fetchImpl, notify)) dirty = true;

  // 3. 古い記録を消す
  for (const [id, e] of Object.entries(state.queue)) {
    const at = Date.parse(e.doneAt || e.detectedAt);
    if (!["new", "dispatched"].includes(e.status) && now - at > KEEP_DONE_HOURS * 3600e3) {
      delete state.queue[id];
      dirty = true;
    }
  }
  for (const book of [state.daily, state.dailyGenre || {}]) {
    for (const day of Object.keys(book)) {
      if (day < jstDate(new Date(now.getTime() - 7 * 86400e3))) {
        delete book[day];
        dirty = true;
      }
    }
  }
  for (const [k, at] of Object.entries(state.notified)) {
    if (now - Date.parse(at) > 7 * 86400e3) {
      delete state.notified[k];
      dirty = true;
    }
  }
  if (dirty) await env.FAST.put("state", JSON.stringify(state));
  log.open = Object.values(state.queue).filter((e) => ["new", "dispatched"].includes(e.status)).length;
  return log;
}

/** 話題の説明（Issue の本文用） */
const describe = (e) => `- ${e.title}\n  - ${e.url}${e.found ? `\n  - 見つけた見出し: ${e.found}` : ""}`;

async function manageQueue(env, state, now, log, R, gh, dry, fetchImpl, notify = async () => false) {
  const open = Object.values(state.queue).filter((e) => e.status === "new" || e.status === "dispatched");
  if (!open.length) return false;
  let dirty = false;
  const tell = async (key, title, body) => {
    if (dry) return;
    try {
      if (await notify(key, title, body)) dirty = true;
    } catch (e) {
      log.errors.notify = String(e).slice(0, 120);
    }
  };
  let runs = null;
  const getRuns = async () => (runs ??= (await gh(`/actions/workflows/${env.WORKFLOW}/runs?per_page=30`)).workflow_runs || []);
  const minutes = (iso) => (now - Date.parse(iso)) / 60000;

  // 起動済み: パイプラインの結果を見る
  const redo = [];
  const dispatched = open.filter((e) => e.status === "dispatched");
  if (dispatched.length) {
    const acks = await readAcks(gh, env, fetchImpl, now);
    for (const e of dispatched) {
      // 今回の起動より前の結果（同じ記事を前に処理したときの記録）は見ない
      const raw = acks[e.id];
      const ack = raw && Date.parse(raw.finishedAt || 0) >= Date.parse(e.dispatchedAt) - 60000 ? raw : null;
      if (ack && TERMINAL.has(ack.outcome)) {
        Object.assign(e, { status: "done", outcome: ack.outcome, slug: ack.slug || "", doneAt: now.toISOString() });
        log.done = [...(log.done || []), `${ack.outcome}: ${e.title.slice(0, 50)}${ack.slug ? ` → ${ack.slug}` : ""}`];
        dirty = true;
        if (ack.outcome === "capped" || ack.outcome === "failed") {
          // パイプライン側の安全上限・失敗で記事にならなかった（黙って落とさない）
          await tell(`ack:${e.id}`, `【自動通知】速報が記事になりませんでした（${ack.outcome}）`,
            `速報レーンで起動した話題が記事になりませんでした（${ack.reason || ack.outcome}）。手動で確認してください。\n\n${describe(e)}`);
        }
        continue;
      }
      const run = (await getRuns()).find(
        (r) => (r.display_title || "").includes(e.runKey || e.url) && Date.parse(r.created_at) >= Date.parse(e.dispatchedAt) - 60000,
      );
      const since = minutes(e.dispatchedAt);
      let again = false;
      let why = "";
      if (ack?.outcome === "retry" && (!run || run.status === "completed")) [again, why] = [true, "作れなかった（再挑戦）"];
      else if (run?.status === "completed") [again, why] = [since >= 3, `実行が ${run.conclusion} で結果の記録なし`];
      else if (!run) [again, why] = [since >= 10, "実行が見つからない"];
      else [again, why] = [since >= 60, "60分たっても終わらない"];
      if (!again) continue;
      if ((e.dispatches || 0) >= R.max_dispatches) {
        Object.assign(e, { status: "gave_up", outcome: why, doneAt: now.toISOString() });
        log.gaveUp = [...(log.gaveUp || []), `${e.title.slice(0, 50)}（${why}）`];
        dirty = true;
        await tell(`gaveup:${e.id}`, "【自動通知】速報を記事にできませんでした（起動を諦めました）",
          `${R.max_dispatches}回起動しても記事になりませんでした（${why}）。GitHub の Actions の「速報レーン」の実行を確認してください。\n\n${describe(e)}`);
        continue;
      }
      e.redoReason = why;
      redo.push(e);
    }
  }

  // 新規（1日の上限を超えた分は capped）
  const day = jstDate(now);
  const cap = Number(env.FAST_DAILY_MAX || R.daily_max);
  const fresh = open.filter((e) => e.status === "new").sort((a, b) => a.detectedAt.localeCompare(b.detectedAt));
  const allowance = Math.max(0, cap - (state.daily[day] || 0));
  // ジャンルごとの上限（genre_caps）。1つのジャンルが合計の上限を食いつぶさないように
  state.dailyGenre ||= {};
  const usedG = { ...(state.dailyGenre[day] || {}) };
  const caps = R.genre_caps || {};
  const over = [];
  let taken = 0;
  for (const e of fresh) {
    const g = e.genre || genreOf(R, e.source);
    const gcap = Number.isFinite(caps[g]) ? caps[g] : Infinity;
    if (taken >= allowance || (usedG[g] || 0) >= gcap) {
      over.push(e);
      continue;
    }
    taken += 1;
    usedG[g] = (usedG[g] || 0) + 1;
  }
  for (const e of over) {
    Object.assign(e, { status: "capped", doneAt: now.toISOString() });
    log.capped = [...(log.capped || []), e.title.slice(0, 50)];
    dirty = true;
  }
  if (over.length) {
    // 安全上限（暴走よけ）に達した。黙って落とさず知らせる（同じ日・同じ上限の知らせは1回。2件目以降はコメントせず KV のログに残る）
    const g = taken >= allowance ? "" : over[0].genre || genreOf(R, over[0].source);
    const what = g ? `ジャンル「${g}」の上限（1日${caps[g]}本）` : `安全上限（1日${cap}本）`;
    await tell(`cap:${day}${g ? `:${g}` : ""}`, `【自動通知】速報レーンの${what}に達しました`,
      `今日（${day}）の速報レーン＋レーダーの起動が${what}に達したため、次の話題を起動していません。\n\n` +
      `${over.map(describe).join("\n")}\n\n本当に大きなニュースなら、Actions の Auto Article Pipeline を fast_url にこの URL を入れて手動で実行してください。` +
      `誤検知が続いている場合は scripts/fast_lane_rules.json を見直してください（上限は Worker の FAST_DAILY_MAX）。`);
  }
  const batch = [...redo, ...fresh.filter((e) => e.status === "new")].slice(0, R.max_per_run);
  if (!batch.length) return dirty;

  // 本文・画像はこちらでも読んで渡す（パイプラインから読めないサイト向け）。読めなくても待たずに起動し、
  // パイプライン側の取得（直接→読み取りサービス）に任せる。openai.com の記事ページは Worker からは 403（2026-10-27 確認）
  const pages = await Promise.all(batch.map((e) => fetchArticle(e.url, fetchImpl, env.JINA_API_KEY)));
  const meta = batch.map((e, k) => ({
    // レーダーの話題で出典が公式なら、まとめサイトの見出し（観測・見立てを含むことがある）ではなく公式ページの題を使う
    id: e.id, url: e.url, title: ((e.via && !e.secondary) || e.pageTitle) && pages[k].title.length >= 8 ? pages[k].title.slice(0, 200) : e.title, source: e.source,
    genre: e.genre || genreOf(R, e.source),
    publishedAt: e.publishedAt || pages[k].published || null, detectedAt: e.detectedAt,
    image: pages[k].image || "", text: pages[k].text || "",
    // レーダーの話題: 出典の名前・報道が出典か（パイプラインが「〇〇の報道によると」と書く）
    ...(e.via ? { sourceName: e.sourceName || "", secondary: Boolean(e.secondary), via: e.via, found: e.found || "" } : {}),
  }));
  const summary = meta.map((x, k) => ({
    url: x.url, title: x.title.slice(0, 60), textChars: x.text.length, image: Boolean(x.image), via: pages[k].via, status: pages[k].status,
  }));

  if (dry) {
    for (const e of batch) Object.assign(e, { status: "dry", doneAt: now.toISOString() });
    log.wouldDispatch = summary.map((x, k) => ({ ...x, reason: batch[k].redoReason || batch[k].reason }));
    return true;
  }

  // 速報レーンの実行が待機中なら、取り消してしまわないよう次の回に回す（通常の回の待機は取り消してよい）
  const waiting = (await getRuns()).find((r) => WAITING.has(r.status) && (r.display_title || "").startsWith(RUN_PREFIX));
  if (waiting) {
    log.waiting = `速報レーンの実行が待機中（#${waiting.run_number}）`;
    return dirty;
  }
  await gh(`/actions/workflows/${env.WORKFLOW}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: "main", inputs: { mode: "web", fast_url: batch[0].url, fast_meta: JSON.stringify(meta) } }),
  });
  for (const e of batch) {
    if (e.status === "new") {
      state.daily[day] = (state.daily[day] || 0) + 1;
      const g = e.genre || genreOf(R, e.source);
      state.dailyGenre[day] = { ...(state.dailyGenre[day] || {}), [g]: ((state.dailyGenre[day] || {})[g] || 0) + 1 };
    }
    Object.assign(e, { status: "dispatched", dispatchedAt: now.toISOString(), dispatches: (e.dispatches || 0) + 1, runKey: batch[0].url });
  }
  log.dispatched = summary.map((x, k) => ({ ...x, redo: batch[k].redoReason || "" }));
  return true;
}
