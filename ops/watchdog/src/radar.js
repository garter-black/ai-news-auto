/**
 * 大きなニュースのレーダー（4分ごと・速報レーンとは別の Cron）。公式フィードが遅れても・載らなくても大きな話題を取りこぼさないための2つ目の網。
 *
 * - まとめサイト（Techmeme・Hacker News・日本の IT メディア）の見出しを見て、主要企業の名前＋強い発表の語があれば候補にする
 *   （判定は scripts/fast_lane_rules.json の radar。Python 側 scripts/radar.py と同じ判定。npm test で突き合わせる）
 * - 直近72時間に同じ話題の記事（state/coverage.json）・速報レーンの登録・レーダーの起動があれば何もしない
 * - 出典は一次情報（公式の発表・SEC の書類）を探して使う。見出しのリンク → 記事ページの中のリンクの順。
 *   見つからなければ信頼できる報道機関（radar.outlets）の記事を出典にする（パイプラインが「〇〇の報道によると」と書く）
 * - 起動は速報レーンに任せる: KV の "radar" の outbox に置くと、速報レーン（fastlane.js）が自分の queue に取り込んで
 *   起動・再起動・結果の確認・1日の上限をまとめて扱う（"state" を書くのは速報レーンだけ）
 * - state/radar_requests.json（毎朝の見逃しチェック・手動の依頼）の URL も同じ流れで回す
 * - 初回は「いまから後に出た見出しだけを見る」印（since）を付けるだけ（デプロイ直後に昔の話題を流さない）
 * - KV への書き込みは候補が出たときだけ（見ただけの見出しは記録しない）
 */
import { buzzPatterns, decode, itemId, jstDate, parseFeed, textOf } from "./fastlane.js";

const UA = "Mozilla/5.0 (compatible; SaisokuRadar/1.0; +https://ai-tech-express.jp)";
const KEEP_HANDLED_HOURS = 96;
const NO_PRIMARY_RETRY_MIN = 30;
const NO_PRIMARY_MAX_TRIES = 4;
const PAGE_CAP = 800000;
// 同じ回に見つけた候補の扱う順（一次情報のリンクが付いていることが多い順）
const SOURCE_ORDER = ["request", "techmeme", "itmedia-news", "itmedia-aiplus", "pc-watch", "ktai-watch", "mynavi", "gori-me", "hn"];

// --------------------------------------------------------------------------- //
// ルール
// --------------------------------------------------------------------------- //

/** 速報の発信元（rules.sources＋sources_registry.json）のホストも公式扱いにする（一覧の食い違いで「対象外のURL」にしない） */
export function withSourceHosts(hosts, sources) {
  const out = [...(hosts || [])];
  const covered = (h) => out.some((p) => h === p.host || h.endsWith(`.${p.host}`));
  for (const s of sources || []) {
    let h;
    try {
      h = new URL(s.url).host.toLowerCase().replace(/^www\./, "");
    } catch {
      continue;
    }
    if (!covered(h)) out.push({ host: h, entity: "*", name: s.name || h });
  }
  return out;
}

/** sources_registry.json の radar_sources（追加の報道・まとめ。見つけるためだけ）を足す。scripts/radar.py の with_radar_sources と同じ */
export function withRadarSources(sources, extra) {
  const have = new Set((sources || []).map((s) => s.slug));
  return [...(sources || []), ...(extra || []).filter((s) => s?.slug && !have.has(s.slug) && !s.disabled)];
}

/** この回に読むか（every 分ごと。レーダーは4分ごとなので、8分なら2回に1回。発信元ごとに回をずらす） */
export function radarDue(src, now) {
  const slots = Math.max(1, Math.round(Number(src.every || 4) / 4));
  if (slots <= 1) return true;
  const tick = Math.floor(now.getTime() / 240000);
  let h = 0;
  for (const c of String(src.slug)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return tick % slots === h % slots;
}

export function compileRadar(rules) {
  const r = rules.radar;
  const re = (s) => new RegExp(s, "i");
  return {
    ...r,
    sources: withRadarSources(r.sources, rules.registry?.radar_sources),
    primary_hosts: withSourceHosts(r.primary_hosts, rules.sources),
    entities: r.entities.map((e) => ({ name: e.name, re: re(e.re), aiOnly: Boolean(e.ai_only) })),
    signal: re(r.signal_re),
    exclude: re(r.exclude_re),
    ai: re(r.ai_re),
    path: new RegExp(r.primary_path_re),
    aliases: new Set(r.aliases),
    versionedOk: new Set(r.versioned_ok),
    stop: new Set(r.stop_words),
    fastSources: rules.sources,
    // 追加の発信元（tier B・C）の会社の名前（速報レーンの「話題待ち」用に、見出しを KV の "buzz" に残す）
    buzz: buzzPatterns(rules),
  };
}

export function entitiesOf(text, RR) {
  return RR.entities.filter((e) => e.re.test(text || "")).map((e) => e.name);
}

/** レーダーに乗せる見出しか。理由の文言は scripts/radar.py の classify() と同じ */
export function classify(title, RR) {
  const t = String(title || "").trim();
  const ex = t.match(RR.exclude);
  if (ex) return { ok: false, reason: `除外語（${ex[0]}）`, entities: [] };
  const hits = RR.entities.filter((e) => e.re.test(t));
  const names = hits.map((e) => e.name);
  if (!hits.length) return { ok: false, reason: "対象の会社・製品がない", entities: [] };
  if (hits.every((e) => e.aiOnly) && !RR.ai.test(t)) return { ok: false, reason: "AI の話題ではない", entities: names };
  if (!RR.signal.test(t)) return { ok: false, reason: "強い発表の語がない", entities: names };
  return { ok: true, reason: "対象", entities: names };
}

// --------------------------------------------------------------------------- //
// 「同じ話題の記事がもうあるか」（scripts/radar.py の features / covered と同じ）
// --------------------------------------------------------------------------- //

const VERSION_RE = /(?<![a-z0-9])([a-z]+)[ -]?(\d+(?:\.\d+)?)(?![0-9])/g;
const BRACKET_RE = /[「『]([^」』]{2,30})[」』]/g;
const KATAKANA_RE = /[ァ-ヴー]{3,}/g;
const WORD_RE = /[A-Za-z][A-Za-z0-9+&]*/g;
const IPO_RE = /(?<![A-Za-z])IPO(?![A-Za-z])|(?<![A-Za-z])S-1(?![0-9])|initial public offering|go(?:es|ing)? public|上場|新規株式公開/i;
const squash = (s) => String(s || "").normalize("NFKC").toLowerCase().replace(/[^0-9a-zぁ-んァ-ヴー一-龥]+/g, "");

export function features(text, RR, strong = false) {
  const t = String(text || "").normalize("NFKC");
  const low = t.toLowerCase();
  const out = new Set();
  const alphas = new Set();
  for (const m of low.matchAll(VERSION_RE)) {
    const [alpha, num] = [m[1], m[2]];
    if (RR.stop.has(alpha) || (RR.aliases.has(alpha) && !RR.versionedOk.has(alpha))) continue;
    out.add(alpha + num);
    alphas.add(alpha);
  }
  for (const m of t.matchAll(BRACKET_RE)) {
    const w = squash(m[1]);
    if (w.length >= 2 && !RR.aliases.has(w)) out.add(w);
  }
  for (const m of t.matchAll(KATAKANA_RE)) {
    const w = m[0].replace(/^ー+|ー+$/g, "");
    if (w.length >= 3 && !RR.stop.has(w) && !RR.aliases.has(w)) out.add(w);
  }
  for (const m of t.matchAll(WORD_RE)) {
    const word = m[0];
    const lw = word.toLowerCase();
    if (lw.length < 3 || RR.stop.has(lw) || RR.aliases.has(lw)) continue;
    if (strong && !/[A-Z]/.test(word)) continue;
    if (strong && alphas.has(lw)) continue; // 「Genie 4」の Genie だけで「Genie 3」の記事と同じ話題にしない
    out.add(lw);
  }
  if (IPO_RE.test(t)) out.add("#ipo");
  return out;
}

// 1語の一致でも同じ話題とみなす語（2文字以上の名前＋版数の製品名・上場のような1社に1回の出来事）。M6・A20 のような1文字の型番は2語目の手がかりにだけ使う
const special = (t) => t.startsWith("#") || /^[a-z]{2,}\d+(?:\.\d+)?$/.test(t);
const STALE_DAYS = 45;
// URL に入っている年月（/2025/10/ など）。古い記事へのリンク（関連記事・前の型の発表）を一次情報にしない
function urlDate(url) {
  const m = String(url).match(/\/(20\d\d)\/(\d{1,2})\//);
  return m && Number(m[2]) >= 1 && Number(m[2]) <= 12 ? Date.UTC(Number(m[1]), Number(m[2]) - 1, 1) : null;
}
export const stale = (url, at) => {
  const d = urlDate(url);
  return d !== null && Number.isFinite(at) && at - d > (STALE_DAYS + 31) * 86400e3;
};

/** dedupe.bigrams と同じ（全角半角・大小をそろえ、記号と空白を落とした2文字ずつ） */
function bigrams(title) {
  const s = String(title || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  if (s.length <= 1) return new Set([s]);
  const out = new Set();
  for (let i = 0; i < s.length - 1; i += 1) out.add(s.slice(i, i + 2));
  return out;
}

function jaccard(a, b) {
  if (!a.size || !b.size) return 0;
  let n = 0;
  for (const x of a) if (b.has(x)) n += 1;
  return n / (a.size + b.size - n);
}

export function normUrl(u) {
  try {
    const x = new URL(String(u || "").trim());
    return `${x.host.toLowerCase().replace(/^www\./, "")}${x.pathname.replace(/\/+$/, "")}`;
  } catch {
    return "";
  }
}

/** 同じページとみなす URL の鍵（正規化した URL と「サイト＋最後の長い部分」。apple.com/newsroom/X と apple.com/jp/newsroom/X を同じにする） */
export function urlKeys(u) {
  const n = normUrl(u);
  if (!n) return [];
  const [host, ...rest] = n.split("/");
  const last = rest.filter(Boolean).at(-1) || "";
  return last.length >= 12 && /[a-z]-[a-z0-9]/.test(last) ? [n, `${host}|${last}`] : [n];
}

/** 記録（{slug, title, head, text, urls}）に判定用の語を足す。head は見出し（記事の題＋元記事の題）、text は見出し＋キーワード */
export function prepare(records, RR) {
  const out = records.map((r) => {
    const text = String(r.text || "");
    const title = String(r.title || text.split(" | ")[0]);
    const head = features(String(r.head || title), RR);
    return {
      ...r,
      _heads: String(r.head || title).split(" | ").map(bigrams),
      _ents: new Set(entitiesOf(text, RR)),
      _tokens: new Set([...features(text, RR), ...head]),
      _head: head,
      _urls: new Set((r.urls || []).flatMap(urlKeys)),
      _title: title,
    };
  });
  const df = new Map();
  for (const r of out) for (const t of r._tokens) df.set(t, (df.get(t) || 0) + 1);
  for (const r of out) r._df = df;
  return out;
}

export function covered(title, urls, records, RR) {
  const wanted = new Set((urls || []).flatMap(urlKeys));
  for (const r of records) for (const u of wanted) if (r._urls.has(u)) return { slug: r.slug || "", reason: "同じ出典URL" };
  const ents = new Set(entitiesOf(title, RR));
  const strong = features(title, RR, true);
  const grams = bigrams(title);
  let best = null;
  for (const r of records) {
    if (ents.size && ![...ents].some((e) => r._ents.has(e))) continue;
    // 1語だけの一致は、記事の見出しに出てくる語で、見出しの会社がすべて記事にも出てくるときに限る
    // （キーワードに機能名が並んでいるだけの記事・別の会社との提携の話で「記事あり」にしない）
    const shared = [...strong].filter((t) => r._tokens.has(t));
    const head = [...strong].filter((t) => r._head.has(t)).sort();
    const allEnts = [...ents].every((e) => r._ents.has(e));
    const sim = Math.max(0, ...r._heads.map((g) => jaccard(grams, g)));
    let why = "";
    if (shared.length >= 2 && head.length) why = `固有名詞が一致（${[...shared].sort().slice(0, 3).join("・")}）`;
    else if (allEnts && head.some(special)) why = `製品名・出来事が一致（${head.find(special)}）`;
    else if (allEnts && head.some((t) => (r._df.get(t) || 0) < 3 && t.length >= 4)) why = `珍しい固有名詞が一致（${head[0]}）`;
    else if (ents.size && head.length && sim >= 0.3) why = `固有名詞＋見出しが類似（${head[0]} / ${sim.toFixed(2)}）`;
    else if (ents.size && sim >= 0.5) why = "見出しが類似";
    if (why && (!best || shared.length > best[0])) best = [shared.length, { slug: r.slug || "", reason: why }];
  }
  return best ? best[1] : null;
}

// --------------------------------------------------------------------------- //
// 一次情報を探す（scripts/radar.py の resolve と同じ考え方）
// --------------------------------------------------------------------------- //

const hostMatch = (host, base) => {
  const h = String(host || "").toLowerCase().replace(/^www\./, "");
  return h === base || h.endsWith(`.${base}`);
};

export function officialOf(url, ents, RR) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (!/^https?:$/.test(u.protocol) || !RR.path.test(u.pathname || "/")) return null;
  return RR.primary_hosts.find((h) => hostMatch(u.host, h.host) && (h.entity === "*" || !ents.length || ents.includes(h.entity))) || null;
}

export function outletOf(url, RR) {
  try {
    const host = new URL(url).host;
    return RR.outlets.find((o) => hostMatch(host, o.host)) || null;
  } catch {
    return null;
  }
}

export function blocked(url, RR) {
  try {
    const host = new URL(url).host;
    return RR.blocked_hosts.some((b) => hostMatch(host, b));
  } catch {
    return true;
  }
}

export function linksIn(html, base) {
  const out = new Set();
  for (const m of String(html).slice(0, PAGE_CAP).matchAll(/<a\b[^>]*?\bhref=["']([^"'#]+)["']/gi)) {
    try {
      out.add(new URL(decode(m[1]).trim(), base).toString());
    } catch {
      // 壊れたリンクは飛ばす
    }
  }
  return [...out];
}

/** 公式の URL の中からいちばん良いもの（会社の公式 > SEC、見出しと語の重なりが多いもの。古い年月の URL は除く） */
export function pickPrimary(cands, ents, title, RR, at = NaN) {
  const words = new Set([...features(title, RR), ...(String(title).match(/[A-Za-z]+/g) || []).map((w) => w.toLowerCase())]);
  let best = null;
  for (const url of cands) {
    const h = officialOf(url, ents, RR);
    if (!h || stale(url, at)) continue;
    const slugWords = new Set(new URL(url).pathname.toLowerCase().match(/[a-z0-9]+/g) || []);
    const score = [h.entity === "*" ? 0 : 1, [...words].filter((w) => slugWords.has(w)).length];
    if (!best || score[0] > best[0][0] || (score[0] === best[0][0] && score[1] > best[0][1])) best = [score, url];
  }
  return best ? best[1] : "";
}

/** 速報レーンの発信元（scripts/fast_lane_rules.json の sources）で、URL の会社のもの（パイプラインが発信元名・順位を引く） */
function fastSourceFor(url, RR) {
  try {
    const host = new URL(url).host.toLowerCase().replace(/^www\./, "");
    const src = RR.fastSources.find((s) => new URL(s.url).host.toLowerCase().replace(/^www\./, "") === host);
    return src ? src.slug : "";
  } catch {
    return "";
  }
}

/** {url, sourceName, secondary, source}。見出しのリンク → 記事ページの中のリンク（最大 pages ページ）→ 報道機関 の順 */
export async function resolvePrimary(group, RR, fetchImpl, pages = 2) {
  const ents = [...new Set(group.flatMap((c) => c.entities || []))];
  const title = group[0].title;
  const at = Math.max(...group.map((c) => Date.parse(c.publishedAt || "") || 0)) || Date.now();
  const links = [...new Set(group.flatMap((c) => c.links))].filter((u) => !blocked(u, RR));
  const company = (u) => u && officialOf(u, ents, RR)?.entity !== "*";
  let best = pickPrimary(links, ents, title, RR, at);
  const fetched = [];
  if (!company(best)) {
    // 会社の公式が見つかるまで記事ページの中のリンクを探す（SEC の書類だけなら次のページも見る）
    const found = best ? [best] : [];
    for (const link of links.filter((u) => !officialOf(u, ents, RR)).slice(0, pages)) {
      try {
        const res = await fetchImpl(link, {
          headers: { "User-Agent": UA, Accept: "text/html,application/xhtml+xml", "Accept-Language": "ja,en;q=0.8" },
          signal: AbortSignal.timeout(12000),
        });
        fetched.push(`${res.status}`);
        if (!res.ok) continue;
        found.push(...linksIn(await res.text(), link));
      } catch (e) {
        fetched.push(String(e).slice(0, 30));
      }
      best = pickPrimary(found, ents, title, RR, at);
      if (company(best)) break;
    }
  }
  if (best) {
    const h = officialOf(best, ents, RR);
    return { url: best, sourceName: h.name, secondary: false, source: fastSourceFor(best, RR) || "radar-official", fetched };
  }
  // 一次情報が無い: 信頼できる報道機関の記事（有料の壁がある媒体は最後）
  const outs = links.filter((u) => outletOf(u, RR)).sort((a, b) => Number(Boolean(outletOf(a, RR).paywall)) - Number(Boolean(outletOf(b, RR).paywall)));
  if (outs.length) {
    const o = outletOf(outs[0], RR);
    return { url: outs[0], sourceName: o.name, secondary: true, source: `radar-${o.host}`, fetched };
  }
  return { url: "", sourceName: "", secondary: false, source: "", fetched };
}

// --------------------------------------------------------------------------- //
// 情報源の読み取り
// --------------------------------------------------------------------------- //

/** Techmeme の RSS: 説明文の太字の見出しのリンク（元の記事）も拾う。末尾の「(媒体名)」は外す */
export function parseTechmeme(xml) {
  const out = [];
  for (const m of String(xml).matchAll(/<item\b[\s\S]*?<\/item>/gi)) {
    const b = m[0];
    const title = textOf((b.match(/<title>([\s\S]*?)<\/title>/i) || [])[1] || "").replace(/\s*\([^()]{2,60}\)\s*$/, "");
    const link = textOf((b.match(/<link>([\s\S]*?)<\/link>/i) || [])[1] || "");
    const desc = (b.match(/<description>([\s\S]*?)<\/description>/i) || [])[1] || "";
    const raw = desc.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1");
    const head = (raw.match(/<B>\s*<A HREF="([^"]+)"/i) || raw.match(/<A HREF="([^"]+)"/i) || [])[1];
    const ts = Date.parse(textOf((b.match(/<pubDate>([\s\S]*?)<\/pubDate>/i) || [])[1] || ""));
    if (!title || !link) continue;
    out.push({ title, url: link, links: head ? [decode(head)] : [], publishedAt: Number.isFinite(ts) ? new Date(ts).toISOString() : null });
  }
  return out;
}

async function getText(url, fetchImpl) {
  const res = await fetchImpl(url, {
    headers: { "User-Agent": UA, Accept: "application/rss+xml, application/xml, application/json, text/html;q=0.9, */*;q=0.8", "Accept-Language": "ja,en;q=0.8" },
    signal: AbortSignal.timeout(12000),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.text();
}

async function readSource(src, fetchImpl) {
  if (src.type === "hn") {
    const ids = JSON.parse(await getText(src.url, fetchImpl)).slice(0, src.top || 20);
    // 記事ごとの取得は Cloudflare のキャッシュに10分残す（毎回の取得数を抑える）
    const items = await Promise.all(
      ids.map(async (id) => {
        try {
          const res = await fetchImpl(`https://hacker-news.firebaseio.com/v0/item/${id}.json`, {
            headers: { "User-Agent": UA }, signal: AbortSignal.timeout(8000), cf: { cacheTtl: 600, cacheEverything: true },
          });
          const d = res.ok ? await res.json() : null;
          if (!d?.title) return null;
          const url = d.url || `https://news.ycombinator.com/item?id=${id}`;
          return { title: d.title, url, links: d.url ? [d.url] : [], publishedAt: new Date((d.time || 0) * 1000).toISOString() };
        } catch {
          return null;
        }
      }),
    );
    return items.filter(Boolean);
  }
  const body = await getText(src.url, fetchImpl);
  if (src.slug === "techmeme") return parseTechmeme(body);
  return parseFeed(body).map((i) => ({ ...i, links: [i.url] }));
}

/** state/coverage.json（パイプラインが記事を作るたびに書く直近の記事の索引）。公開の raw（最大5分古い）で読む */
async function readCoverage(env, fetchImpl, now) {
  try {
    const res = await fetchImpl(`https://raw.githubusercontent.com/${env.REPO}/main/state/coverage.json?t=${Math.floor(now.getTime() / 60000)}`, {
      cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    return res.ok ? (await res.json()).items || [] : null;
  } catch {
    return null;
  }
}

/** state/radar_requests.json（毎朝の見逃しチェック・手動の依頼） */
async function readRequests(env, fetchImpl, now) {
  try {
    const res = await fetchImpl(`https://raw.githubusercontent.com/${env.REPO}/main/state/radar_requests.json?t=${Math.floor(now.getTime() / 60000)}`, {
      cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    return res.ok ? (await res.json()).items || [] : [];
  } catch {
    return [];
  }
}

// --------------------------------------------------------------------------- //
// 1回分
// --------------------------------------------------------------------------- //

/**
 * @param env  KV（FAST）・REPO・RADAR_DRY_RUN・RADAR_DAILY_MAX
 * @param deps テスト用の差し替え { rules, fetch, notify(key, title, body) }
 */
export async function radar(env, now, deps = {}) {
  const RR = compileRadar(deps.rules);
  const fetchImpl = deps.fetch || fetch;
  const dry = String(env.RADAR_DRY_RUN || "") === "1";
  const st = (await env.FAST.get("radar", "json")) || {};
  const log = { dry, read: {}, errors: {}, candidates: [], covered: [], dispatched: [], noPrimary: [], deferred: [] };
  if (!st.since) {
    // 初回: いまから後に出た見出しだけを見る
    await env.FAST.put("radar", JSON.stringify({ since: now.toISOString(), handled: {}, outbox: {}, daily: {}, notified: {} }));
    return { ...log, baseline: now.toISOString() };
  }
  st.handled ||= {};
  st.outbox ||= {};
  st.daily ||= {};
  st.notified ||= {};
  let dirty = false;
  const since = Math.max(Date.parse(st.since), now.getTime() - RR.max_age_hours * 3600e3);

  // 1. 読む（追加の報道・まとめは every 分ごと。1回の取得数を抑える）
  const sources = RR.sources.filter((src) => radarDue(src, now));
  const results = await Promise.all(sources.map((src) => readSource(src, fetchImpl).catch((e) => ({ error: String(e).slice(0, 80) }))));
  const items = [];
  for (const [k, src] of sources.entries()) {
    const r = results[k];
    if (r.error) {
      log.errors[src.slug] = r.error;
      continue;
    }
    log.read[src.slug] = r.length;
    for (const it of r) {
      const at = it.publishedAt ? Date.parse(it.publishedAt) : NaN;
      if (!Number.isFinite(at) || at < since || at > now.getTime() + 3600e3) continue;
      items.push({ ...it, source: src.slug, sourceName: src.name });
    }
  }
  try {
    await recordBuzz(env, items, RR, now, log, dry);
  } catch (e) {
    log.errors.buzz = String(e).slice(0, 80);
  }
  const ids = await Promise.all(items.map((it) => itemId(it.url)));
  const open = (id) => {
    const h = st.handled[id];
    return !h || (h.status === "no-primary" && Date.parse(h.retryAt) <= now.getTime());
  };
  const cands = [];
  for (const [k, it] of items.entries()) {
    if (!open(ids[k])) continue;
    const v = classify(it.title, RR);
    if (!v.ok) continue;
    cands.push({ ...it, id: ids[k], entities: v.entities });
    log.candidates.push(`${it.source}: ${it.title.slice(0, 80)}`);
  }
  // 見逃しチェック・手動の依頼（判定は済んでいるので見出しの判定はしない）
  for (const q of await readRequests(env, fetchImpl, now)) {
    if (!q?.url || Date.parse(q.requestedAt || 0) < now.getTime() - 48 * 3600e3) continue;
    const id = `req:${await itemId(q.url)}`;
    const ok = officialOf(q.url, [], RR) || outletOf(q.url, RR);
    // An official host may have been added since this request was rejected.
    // Reconsider only that explicit rejection; covered/dispatched requests stay closed.
    const h = st.handled[id];
    const newlyAllowed = h?.status === "rejected" && h.reason === "対象外のURL" && ok && !blocked(q.url, RR);
    if (!open(id) && !newlyAllowed) continue;
    if (!ok || blocked(q.url, RR)) {
      st.handled[id] = { at: now.toISOString(), status: "rejected", reason: "対象外のURL", title: String(q.title || "").slice(0, 80) };
      dirty = true;
      continue;
    }
    cands.push({
      title: String(q.title || q.url), url: q.url, links: [q.url], id, source: "request", sourceName: q.via || "依頼",
      entities: entitiesOf(String(q.title || ""), RR), request: q, publishedAt: null,
    });
    log.candidates.push(`request: ${String(q.title || q.url).slice(0, 80)}`);
  }
  if (!cands.length) {
    if (prune(st, now)) dirty = true;
    if (dirty) await env.FAST.put("radar", JSON.stringify(st));
    return log;
  }

  // 2. 同じ話題の記事・登録があるか（記事の索引・速報レーンの queue・レーダーの起動）
  const [coverage, fast] = await Promise.all([readCoverage(env, fetchImpl, now), env.FAST.get("state", "json")]);
  if (coverage === null) log.errors.coverage = "記事の索引を読めない（URL と速報レーンの登録だけで判定）";
  const horizon = now.getTime() - RR.coverage_hours * 3600e3;
  // 速報レーンの登録は、処理中か記事になった（または価値なしと判定された）ものだけ「記事あり」に数える。諦めた・上限で止めたものは数えない
  const counted = (e) => ["new", "dispatched"].includes(e.status) || (e.status === "done" && !["failed", "capped", "retry"].includes(e.outcome));
  const records = [
    ...(coverage || []).filter((c) => Date.parse(c.at) >= horizon),
    ...Object.values(fast?.queue || {}).filter((e) => Date.parse(e.detectedAt) >= horizon && counted(e))
      .map((e) => ({ slug: `fast:${e.slug || e.id}`, title: e.title, text: e.title, urls: [e.url], fast: true })),
    ...Object.values(st.outbox).filter((e) => Date.parse(e.detectedAt) >= horizon)
      .map((e) => ({ slug: `radar:${e.id}`, title: e.title, text: e.title, urls: [e.url, e.found] })),
  ];
  let prepared = prepare(records, RR);
  // 見逃しチェック・手動の依頼は、記事の索引とレーダーの起動だけで判定する（速報レーンで記事にならずに終わった話題も作り直す）
  const preparedReq = prepare(records.filter((r) => !r.fast), RR);

  // 同じ回の同じ話題はまとめる（リンクを合わせて一次情報を探す）
  const order = (s) => (SOURCE_ORDER.includes(s) ? SOURCE_ORDER.indexOf(s) : SOURCE_ORDER.length - 1);
  cands.sort((a, b) => order(a.source) - order(b.source));
  const groups = [];
  for (const c of cands) {
    const hit = covered(c.title, c.links, c.request ? preparedReq : prepared, RR);
    if (hit) {
      st.handled[c.id] = { at: now.toISOString(), status: "covered", by: hit.slug, reason: hit.reason, title: c.title.slice(0, 80) };
      log.covered.push(`${c.title.slice(0, 60)} → ${hit.slug}（${hit.reason}）`);
      dirty = true;
      continue;
    }
    const mine = prepare(groups.map((g, n) => {
      const titles = g.map((x) => x.title).join(" | ");
      return { slug: String(n), title: g[0].title, head: titles, text: titles, urls: g.flatMap((x) => x.links) };
    }), RR);
    const same = covered(c.title, c.links, mine, RR);
    if (same) groups[Number(same.slug)].push(c);
    else groups.push([c]);
  }

  // 3. 一次情報を探して outbox へ（1回 max_per_run 件まで。残りは次の回）
  const day = jstDate(now);
  const cap = Number(env.RADAR_DAILY_MAX || RR.daily_max);
  let budget = RR.max_per_run;
  for (const g of groups) {
    const head = g[0];
    if (budget <= 0) {
      log.deferred.push(head.title.slice(0, 60));
      continue;
    }
    if ((st.daily[day] || 0) >= cap) {
      for (const c of g) st.handled[c.id] = { at: now.toISOString(), status: "capped", title: c.title.slice(0, 80) };
      log.capped = [...(log.capped || []), head.title.slice(0, 60)];
      dirty = true;
      if (!st.notified[`cap:${day}`] && deps.notify && !dry) {
        await deps.notify(`radar-cap:${day}`, `【自動通知】レーダーの1日の上限（${cap}本）に達しました`,
          `今日（${day}）のレーダーの起動が上限 ${cap} 本に達したため、次の話題を速報レーンに回していません。\n\n- ${head.title}\n- 見出し: ${head.url}\n\n` +
          `誤検知が多い場合は scripts/fast_lane_rules.json の radar を見直してください。本当に大きなニュースが多い日なら、Worker の RADAR_DAILY_MAX を上げてください。`);
        st.notified[`cap:${day}`] = now.toISOString();
      }
      continue;
    }
    budget -= 1;
    const req = head.request;
    const prim = req
      ? { url: req.url, sourceName: req.sourceName || officialOf(req.url, [], RR)?.name || outletOf(req.url, RR)?.name || "",
          secondary: Boolean(req.secondary ?? !officialOf(req.url, [], RR)), source: fastSourceFor(req.url, RR) || (officialOf(req.url, [], RR) ? "radar-official" : `radar-${outletOf(req.url, RR)?.host}`), fetched: [] }
      : await resolvePrimary(g, RR, fetchImpl);
    if (!prim.url) {
      for (const c of g) {
        const tries = (st.handled[c.id]?.tries || 0) + 1;
        st.handled[c.id] = {
          at: now.toISOString(), status: tries >= NO_PRIMARY_MAX_TRIES ? "gave-up" : "no-primary", tries,
          retryAt: new Date(now.getTime() + NO_PRIMARY_RETRY_MIN * 60000).toISOString(), title: c.title.slice(0, 80), found: c.url,
        };
      }
      log.noPrimary.push(`${head.title.slice(0, 60)}（ページ取得 ${prim.fetched.join(",") || "なし"}）`);
      dirty = true;
      continue;
    }
    const pid = await itemId(prim.url);
    const again = covered(head.title, [prim.url], req ? preparedReq : prepared, RR);
    const inFast = fast?.queue?.[pid] && (!req || ["new", "dispatched"].includes(fast.queue[pid].status) || ["created", "updated", "duplicate"].includes(fast.queue[pid].outcome));
    if (inFast || st.outbox[pid] || (again && again.reason === "同じ出典URL")) {
      for (const c of g) st.handled[c.id] = { at: now.toISOString(), status: "covered", by: again?.slug || `fast:${pid}`, reason: "同じ出典URL", title: c.title.slice(0, 80) };
      log.covered.push(`${head.title.slice(0, 60)} → 同じ出典URL（${prim.url}）`);
      dirty = true;
      continue;
    }
    const entry = {
      id: pid, url: prim.url, title: head.title.slice(0, 200), source: prim.source, sourceName: prim.sourceName,
      secondary: prim.secondary, via: req ? `request:${req.via || "manual"}` : head.source, found: head.url, entities: head.entities,
      publishedAt: head.publishedAt || null, detectedAt: now.toISOString(),
    };
    log.dispatched.push({ title: entry.title.slice(0, 80), url: entry.url, sourceName: entry.sourceName, secondary: entry.secondary, via: entry.via, also: g.length - 1 });
    if (dry) continue;
    st.outbox[pid] = entry;
    st.daily[day] = (st.daily[day] || 0) + 1;
    for (const c of g) st.handled[c.id] = { at: now.toISOString(), status: "dispatched", id: pid, title: c.title.slice(0, 80) };
    prepared = prepare([...records, { slug: `radar:${pid}`, title: entry.title, text: entry.title, urls: [entry.url, entry.found] }], RR);
    dirty = true;
  }
  if (prune(st, now)) dirty = true;
  if (dirty && !dry) await env.FAST.put("radar", JSON.stringify(st));
  return log;
}

const BUZZ_KEEP_HOURS = 12;
const BUZZ_MAX = 20;

/** まとめサイトの見出しのうち、追加の発信元の会社の名前が出たものを KV の "buzz" に残す（{entity: [{at, t, src}]}・新しいものがあるときだけ書く） */
export async function recordBuzz(env, items, RR, now, log = {}, dry = false) {
  const ents = Object.entries(RR.buzz || {});
  if (!ents.length) return 0;
  const from = now.getTime() - BUZZ_KEEP_HOURS * 3600e3;
  const st = (await env.FAST.get("buzz", "json")) || {};
  let added = 0;
  let changed = false;
  for (const it of items) {
    const at = Date.parse(it.publishedAt || "") || now.getTime();
    if (at < from) continue;
    for (const [ent, re] of ents) {
      if (!re.test(it.title)) continue;
      const list = (st[ent] ||= []);
      const t = String(it.title).slice(0, 160);
      if (list.some((b) => b.t === t)) continue;
      list.unshift({ at: new Date(at).toISOString(), t, src: it.sourceName || it.source || "" });
      added += 1;
    }
  }
  for (const [ent, list] of Object.entries(st)) {
    const kept = list.filter((b) => Date.parse(b.at) >= from).slice(0, BUZZ_MAX);
    if (kept.length !== list.length) changed = true;
    if (kept.length) st[ent] = kept;
    else delete st[ent];
  }
  if (added) log.buzz = added;
  if ((added || changed) && !dry) await env.FAST.put("buzz", JSON.stringify(st));
  return added;
}

function prune(st, now) {
  let changed = false;
  const old = now.getTime() - KEEP_HANDLED_HOURS * 3600e3;
  for (const [id, h] of Object.entries(st.handled)) {
    if (Date.parse(h.at) < old) {
      delete st.handled[id];
      changed = true;
    }
  }
  for (const [id, e] of Object.entries(st.outbox)) {
    if (Date.parse(e.detectedAt) < old) {
      delete st.outbox[id];
      changed = true;
    }
  }
  for (const day of Object.keys(st.daily)) {
    if (day < jstDate(new Date(now.getTime() - 7 * 86400e3))) {
      delete st.daily[day];
      changed = true;
    }
  }
  for (const [k, at] of Object.entries(st.notified)) {
    if (Date.parse(at) < now.getTime() - 7 * 86400e3) {
      delete st.notified[k];
      changed = true;
    }
  }
  return changed;
}

