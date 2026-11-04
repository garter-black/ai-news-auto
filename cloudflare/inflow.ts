/**
 * 記事に外から来た人の自前集計（流入元 × 記事 × utm_campaign / utm_content）。cloudflare/aff.ts が /api/aff/view・/api/aff/inflow をここに回す。
 * Cloudflare Web Analytics は参照元ごとの合計しか出ず、「直接」に自社の Lighthouse・確認作業が混ざるので、投稿ごとの実数を見るために数える。
 *
 *   POST /api/aff/view                     入口の1表示（画面 components/InflowBeacon.tsx から。同じオリジンだけ）
 *   GET  /api/aff/inflow?date=YYYY-MM-DD   その日（日本時間）の集計（Bearer AFF_STATS_TOKEN。scripts/inflow_report.py が読む）
 *
 * 保存先は AFF_KV（saisoku-aff）:
 *   i:<日付>:<時分秒><乱数>   入口の1表示 = 1キー（KV は同じキーへの書き込みが1秒1回までで、数を足し合わせると取りこぼすため）。
 *                             値は空、metadata に {p: 記事, s: 流入元, c: utm_campaign, t: utm_content, v: 訪問者の短いハッシュ}。90日で消える
 *   is:<日付>                 締まった日の集計結果（1日1回だけ書く。400日残す）。次からは i: を読まずにこれを返す
 * 書き込みは「数えた表示1回 = 1回」。同じ人が同じ記事を30分以内に開き直したら数えない（Cache API）。
 * KV（有料プラン）の書き込みは月100万回まで込み。今の訪問数（1日数百〜数千）なら月10万回前後で余裕がある。
 * IP アドレスは保存しない。訪問者のハッシュは「秘密の値 + 日付 + IP + UA」の SHA-256 先頭12桁で、日をまたいで同じ人をたどれない。
 * 数えないもの: 機械・プレビュー・自動ブラウザの UA、ヘッダー X-Saisoku-Internal: 1、?_internal=1 のページから来た送信、JS が動かないアクセス。
 */
import { json, readJson, safeEqual, sameOrigin, sha256Hex, type KvNamespace } from "./push";
import { INFLOW_BOT_UA, INFLOW_SOURCES, INTERNAL_HEADER, INTERNAL_PARAM, validPageKey, validTag, type InflowSource } from "../lib/inflow";
import { recordVideoVisit } from "./video";

export type InflowEnv = { AFF_KV?: KvNamespace; AFF_STATS_TOKEN?: string };
type Ctx = { waitUntil(promise: Promise<unknown>): void };
type Meta = { p?: string; s?: string; c?: string; t?: string; v?: string };

const KEEP_DAYS = 90;
const SUMMARY_DAYS = 400;
/** 同じ人が同じ記事を開き直したら、この秒数のあいだは1回と数える */
const DEDUPE_SEC = 1800;
/** 同じ人の記録はこの回数まで（10分で戻る）。機械的な連投で KV の書き込みを使い切らないため */
const BURST_MAX = 30;
const BURST_SEC = 600;
const MAX_KEYS = 200_000;

const JST = 9 * 3600_000;
const jstDate = (ms = Date.now()) => new Date(ms + JST).toISOString().slice(0, 10);
const jstTime = (ms = Date.now()) => new Date(ms + JST).toISOString().slice(11, 19).replace(/:/g, "");
const noContent = () => new Response(null, { status: 204, headers: { "Cache-Control": "no-store" } });

/** 自社のテスト・確認（ヘッダー、または ?_internal=1 を付けたページからの送信） */
export function isInternal(request: Request): boolean {
  if (request.headers.get(INTERNAL_HEADER) === "1") return true;
  try {
    const ref = request.headers.get("referer");
    return !!ref && new URL(ref).searchParams.get(INTERNAL_PARAM) === "1";
  } catch {
    return false;
  }
}

async function duplicate(origin: string, visitor: string, key: string): Promise<boolean> {
  try {
    const cache = (caches as unknown as { default: Cache }).default;
    const dedupe = new Request(`${origin}/api/aff/_rl/i/${visitor}/${(await sha256Hex(key)).slice(0, 16)}`);
    if (await cache.match(dedupe)) return true;
    const counter = new Request(`${origin}/api/aff/_rl/in/${visitor}`);
    const hit = await cache.match(counter);
    const n = hit ? Number(await hit.text()) || 0 : 0;
    if (n >= BURST_MAX) return true;
    await Promise.all([
      cache.put(dedupe, new Response("1", { headers: { "Cache-Control": `max-age=${DEDUPE_SEC}` } })),
      cache.put(counter, new Response(String(n + 1), { headers: { "Cache-Control": `max-age=${BURST_SEC}` } })),
    ]);
    return false;
  } catch {
    return false;
  }
}

async function view(request: Request, url: URL, env: InflowEnv, ctx: Ctx): Promise<Response> {
  if (request.method !== "POST") return json({ error: "method" }, 405);
  const site = request.headers.get("sec-fetch-site");
  if (!sameOrigin(request) || (site && site !== "same-origin")) return json({ error: "origin" }, 403);
  const ua = request.headers.get("user-agent") ?? "";
  if (!ua || INFLOW_BOT_UA.test(ua) || isInternal(request) || !env.AFF_KV || !env.AFF_STATS_TOKEN) return noContent();

  const body = await readJson<Record<string, unknown>>(request, 1024);
  const field = (key: string) => (typeof body?.[key] === "string" ? (body[key] as string) : "");
  const page = field("p"), source = field("s"), campaign = field("c"), content = field("t");
  if (!validPageKey(page) || !INFLOW_SOURCES.includes(source as InflowSource) || !validTag(campaign) || !validTag(content)) {
    return json({ error: "invalid" }, 400);
  }

  const now = Date.now();
  const date = jstDate(now);
  // Do Not Track / GPC のブラウザも数だけは数えるが、訪問者のハッシュは作らない
  const anonymous = request.headers.get("dnt") === "1" || request.headers.get("sec-gpc") === "1";
  const visitor = (await sha256Hex(`${env.AFF_STATS_TOKEN}|${date}|${request.headers.get("cf-connecting-ip") ?? ""}|${ua}`)).slice(0, 12);
  if (await duplicate(url.origin, visitor, `${page}|${source}|${campaign}|${content}`)) return noContent();

  const rand = crypto.getRandomValues(new Uint32Array(1))[0].toString(36);
  const meta: Meta = { p: page, s: source, ...(campaign ? { c: campaign } : {}), ...(content ? { t: content } : {}), ...(anonymous ? {} : { v: visitor }) };
  ctx.waitUntil(
    env.AFF_KV.put(`i:${date}:${jstTime(now)}${rand}`, "", { expirationTtl: KEEP_DAYS * 86400, metadata: meta }).catch((error) =>
      console.error("[inflow] put failed", String(error)),
    ),
  );
  // SNS の動画投稿は、短い版と全部の版の比べ（scripts/sns_video_followup.py --ab-report）にも数える。
  // 2026-11-05〜 utm_campaign=sns_video_short|sns_video_full・utm_content=<種類>_<位置>（台帳 S29）。以前の utm_campaign=sns_video・utm_content=short|full も数える
  const variant = videoVariant(campaign, content);
  if (variant && !page.startsWith("digest-") && page !== "_home") {
    recordVideoVisit(env.AFF_KV, page, variant, ctx, now);
  }
  return noContent();
}

/** 動画の版（short / full）。新しい形 utm_campaign=sns_video_<版> と、以前の形 utm_campaign=sns_video&utm_content=<版> */
export function videoVariant(campaign: string, content: string): "short" | "full" | null {
  if (campaign === "sns_video_short" || (campaign === "sns_video" && content === "short")) return "short";
  if (campaign === "sns_video_full" || (campaign === "sns_video" && content === "full")) return "full";
  return null;
}

type Row = { views: number; visitors: Set<string>; hours: Record<string, number> };
const newRow = (): Row => ({ views: 0, visitors: new Set(), hours: {} });

/** その日の i: を読んで、流入元別・記事別・utm 別にまとめる */
export async function summarize(date: string, kv: KvNamespace) {
  const total = newRow();
  const bySource = new Map<string, Row>();
  const rows = new Map<string, Row>();
  let count = 0;
  let cursor: string | undefined;
  do {
    const page = await kv.list({ prefix: `i:${date}:`, cursor, limit: 1000 });
    for (const key of page.keys) {
      const meta = (key.metadata ?? {}) as Meta;
      if (!meta.p || !meta.s) continue;
      count++;
      const hour = key.name.slice(`i:${date}:`.length, `i:${date}:`.length + 2) || "??";
      for (const row of [total, getRow(bySource, meta.s), getRow(rows, [meta.p, meta.s, meta.c ?? "", meta.t ?? ""].join("|"))]) {
        row.views++;
        if (meta.v) row.visitors.add(meta.v);
        row.hours[hour] = (row.hours[hour] ?? 0) + 1;
      }
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor && count < MAX_KEYS);
  return {
    date,
    timezone: "Asia/Tokyo",
    version: 1,
    definition:
      "Landing page views (first page of a browser tab) of articles, digests and the top page from browsers that ran JavaScript. Excludes known bots, link previewers, automated/headless browsers, Lighthouse/PageSpeed and internal tests (?_internal=1 / X-Saisoku-Internal). Same visitor + page + source within 30 min counts once. Visitors are a daily IP/UA hash estimate; do not sum visitors across rows or days.",
    truncated: !!cursor,
    views: total.views,
    visitors: total.visitors.size,
    byHour: total.hours,
    bySource: [...bySource].map(([source, row]) => ({ source, views: row.views, visitors: row.visitors.size })).sort((a, b) => b.views - a.views),
    rows: [...rows]
      .map(([key, row]) => {
        const [page, source, campaign, content] = key.split("|");
        return { page, source, campaign, content, views: row.views, visitors: row.visitors.size, hours: row.hours };
      })
      .sort((a, b) => b.views - a.views),
  };
}

function getRow(map: Map<string, Row>, key: string): Row {
  let row = map.get(key);
  if (!row) map.set(key, (row = newRow()));
  return row;
}

async function stats(request: Request, url: URL, env: InflowEnv): Promise<Response> {
  if (request.method !== "GET") return json({ error: "method" }, 405);
  const expected = env.AFF_STATS_TOKEN ?? "";
  const header = request.headers.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (expected.length < 16 || !safeEqual(token, expected)) return json({ error: "unauthorized" }, 401);
  if (!env.AFF_KV) return json({ error: "AFF_KV がありません" }, 503);
  const date = url.searchParams.get("date") || jstDate();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return json({ error: "date は YYYY-MM-DD" }, 400);

  const fresh = url.searchParams.get("fresh") === "1";
  // 15分前の時点で前日以前になっている日は締まっている（送信の遅れを見込む）
  const closed = date < jstDate(Date.now() - 15 * 60_000);
  if (closed && !fresh) {
    const saved = await env.AFF_KV.get(`is:${date}`);
    if (saved) return new Response(saved, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Inflow-Summary": "saved" } });
  }
  const summary = await summarize(date, env.AFF_KV);
  const body = JSON.stringify({ ...summary, closed });
  if (closed && !summary.truncated && summary.views > 0) {
    await env.AFF_KV.put(`is:${date}`, body, { expirationTtl: SUMMARY_DAYS * 86400 }).catch((error) => console.error("[inflow] summary put failed", String(error)));
  }
  return new Response(body, { headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
}

export async function handleInflow(request: Request, url: URL, env: InflowEnv, ctx: Ctx): Promise<Response> {
  if (url.pathname === "/api/aff/view") return view(request, url, env, ctx);
  if (url.pathname === "/api/aff/inflow") return stats(request, url, env);
  return json({ error: "not found" }, 404);
}
