/**
 * サイソクの見張り番（Cloudflare Workers の Cron Triggers で2分ごとに動く）
 *
 * Q. 地震・津波（毎回）: 気象庁の防災情報XMLで震度5弱以上・津波警報が出たら記事作りを起動する（src/quake.js。LLM は使わない）
 * 0. 速報レーン（毎回）: 主要企業の公式ニュースルームの新着を見て、重要な発表ならすぐ記事作りを起動する（src/fastlane.js）
 *    追加の発信元（scripts/sources_registry.json）は tier ごとに A=毎回・B=10分・C=30分おき
 * R. レーダー（別の Cron・4分ごと）: まとめサイトで大きなニュースを見つけ、一次情報を探して速報レーンに回す（src/radar.js）
 * M. 毎朝 6:00（日本時間）: 深夜（1〜5時）に作った速報を SNS に出す breaking_post.yml を起動する
 *
 * 以下は10分ごと（UTC の分が10の倍数の回）:
 * 1. GitHub Actions の記事作り（auto_run.yml）が START_GAP_MIN 分以上始まっていなければ、自分で起動する
 *    - 6時間ごとの枠（UTC 0/6/12/18時台の最初）に当たらなければ mode=web（軽い回）
 * 2. 成功した回が ALERT_GAP_MIN 分以上無ければ、GitHub に Issue を立ててオーナーにメールで知らせる
 *    （すでに開いている知らせがあれば重ねて立てない。回復したら自動で閉じる）
 *
 * 必要な秘密情報: GH_TOKEN（このリポジトリの Actions の読み書きと Issues の書き込みだけを許可した細かい権限のトークン）
 */
import { fastLane, makeGh, makeNotify, mergeRegistry } from "./fastlane.js";
import { radar } from "./radar.js";
import { quakeLane } from "./quake.js";
import { snsExtra } from "./sns-extra.js";
import { bufferMonitorWatchdog } from "./buffer-monitor.js";
import { checkActivity } from "./activity-monitor.js";
// Python 側（scripts/fast_lane.py）と同じルールを使う。変えたら npx wrangler deploy し直す
import baseRules from "../../../scripts/fast_lane_rules.json";
// 追加の発信元（tier A/B/C）。fast_lane_rules.json の sources に足して使う
import registry from "../../../scripts/sources_registry.json";

const rules = mergeRegistry(baseRules, registry);

const ALERT_TITLE = "【自動通知】記事の自動作成が止まっています";
// wrangler.toml の crons と同じ文字列（どちらの Cron で起きたかを見分ける）
const RADAR_CRON = "1-59/4 * * * *";
// 投稿回収は別の呼び出しにして、速報・災害レーンの時間や取得上限を消費しない。
const SNS_CRON = "3-59/10 * * * *";

async function gh(env, path, init = {}) {
  const res = await fetch(`https://api.github.com/repos/${env.REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "saisoku-watchdog",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
  if (!res.ok && res.status !== 204) throw new Error(`GitHub ${path} -> ${res.status} ${await res.text()}`);
  return res.status === 204 ? null : res.json();
}

async function check(env, now = new Date(), opts = {}) {
  const runs = (await gh(env, `/actions/workflows/${env.WORKFLOW}/runs?per_page=20`)).workflow_runs || [];
  const minutesSince = (iso) => (now - new Date(iso)) / 60000;
  const latestStart = runs[0] ? minutesSince(runs[0].created_at) : Infinity;
  const active = runs.some((r) => r.status === "queued" || r.status === "in_progress");
  const lastSuccess = runs.find((r) => r.conclusion === "success");
  const sinceSuccess = lastSuccess ? minutesSince(lastSuccess.updated_at) : Infinity;
  const log = { latestStart: Math.round(latestStart), active, sinceSuccess: Math.round(sinceSuccess) };

  // 1. 起動
  if (!active && !opts.skipDispatch && latestStart >= Number(env.START_GAP_MIN)) {
    // 6時間ごとの重い回は、UTC 0/6/12/18時台の最初の30分だけ
    const full = now.getUTCHours() % 6 === 0 && now.getUTCMinutes() < 30;
    await gh(env, `/actions/workflows/${env.WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main", inputs: { mode: full ? "full" : "web" } }),
    });
    log.dispatched = full ? "full" : "web";
  }

  // 2. 知らせる／回復したら閉じる
  const open = (await gh(env, `/issues?state=open&labels=watchdog&per_page=5`)) || [];
  if (sinceSuccess >= Number(env.ALERT_GAP_MIN)) {
    if (open.length === 0) {
      await gh(env, `/issues`, {
        method: "POST",
        body: JSON.stringify({
          title: ALERT_TITLE,
          labels: ["watchdog"],
          body:
            `最後に記事作りが成功してから約${Math.round(sinceSuccess)}分たっています。\n\n` +
            `見張り番が自動で起動を試みていますが、成功していません。` +
            `GitHub の Actions 画面で失敗の内容を確認してください。\n\n` +
            `https://github.com/${env.REPO}/actions/workflows/${env.WORKFLOW}`,
        }),
      });
      log.alert = "opened";
    }
  } else {
    for (const issue of open) {
      await gh(env, `/issues/${issue.number}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: "記事作りが再開したので、この知らせを閉じます。" }),
      });
      await gh(env, `/issues/${issue.number}`, { method: "PATCH", body: JSON.stringify({ state: "closed" }) });
      log.alert = "closed";
    }
  }
  return log;
}

async function tick(env, now) {
  // 地震・津波（気象庁）。安全の情報なので速報レーンより先に見る
  let quaked = false;
  try {
    const q = await quakeLane(env, now, { rules, gh: makeGh(env) });
    quaked = Boolean(q?.dispatched.length && !q.dry);
    if (q && (q.fresh || q.dispatched.length || q.error || q.skip || q.baseline)) console.log(JSON.stringify({ quake: q }));
  } catch (e) {
    console.error(`quake: ${String(e)}`);
  }
  let fast = null;
  try {
    // 地震の記事作りをいま起動したなら、速報レーンは次の回に（同じ回に続けて起動すると待機中の地震の実行を取り消しかねない）
    fast = quaked ? { dispatched: null, skipped: "地震の起動を優先" } : await fastLane(env, now, { rules });
    console.log(JSON.stringify({ fast }));
  } catch (e) {
    console.error(`fast: ${String(e)}`);
  }
  if (now.getUTCMinutes() % 10 !== 0) return;
  try {
    console.log(JSON.stringify({ activityMonitor: await checkActivity(env, now, makeGh(env)) }));
  } catch (e) {
    console.error(`activityMonitor: ${String(e)}`);
  }
  if (now.getUTCHours() === 21 && now.getUTCMinutes() === 0) {
    // 日本時間 6:00: 深夜に作って出せなかった速報を SNS に出す（scripts/breaking_post.py が朝の分を拾う）
    try {
      await gh(env, `/actions/workflows/${env.BREAKING_WORKFLOW || "breaking_post.yml"}/dispatches`, {
        method: "POST", body: JSON.stringify({ ref: "main" }),
      });
      console.log(JSON.stringify({ morning: "breaking_post dispatched" }));
    } catch (e) {
      console.error(`morning: ${String(e)}`);
    }
  }
  try {
    // 速報レーンがいま起動したなら、通常の回は重ねて起動しない
    const l = await check(env, now, { skipDispatch: Boolean(fast?.dispatched) || quaked });
    console.log(JSON.stringify({ watchdog: l }));
  } catch (e) {
    console.error(`watchdog: ${String(e)}`);
  }
}

async function radarTick(env, now) {
  try {
    const st = { notified: {} };
    const notify = makeNotify(makeGh(env), st.notified, now);
    const l = await radar(env, now, { rules, notify });
    console.log(JSON.stringify({ radar: l }));
  } catch (e) {
    console.error(`radar: ${String(e)}`);
  }
}

async function snsTick(env, now) {
  try {
    const result = await snsExtra(env, now);
    console.log(JSON.stringify({ snsExtra: result }));
  } catch (e) {
    console.error(`snsExtra: ${String(e)}`);
  }
}

async function bufferMonitorTick(env, now) {
  try {
    const result = await bufferMonitorWatchdog(env, now);
    if (result.error) console.error(JSON.stringify({ bufferMonitorWatchdog: result }));
    else console.log(JSON.stringify({ bufferMonitorWatchdog: result }));
  } catch {
    console.error('bufferMonitorWatchdog: unexpected error');
  }
}

export default {
  async scheduled(event, env, ctx) {
    const now = new Date(event.scheduledTime);
    if (event.cron === SNS_CRON) {
      // 別のwaitUntilでSNS回収の成功・失敗や待機時間と分離する。
      ctx.waitUntil(snsTick(env, now));
      ctx.waitUntil(bufferMonitorTick(env, now));
    } else {
      ctx.waitUntil(event.cron === RADAR_CRON ? radarTick(env, now) : tick(env, now));
    }
  },
  // 動作確認用（外から呼べる URL は無い。wrangler dev のときだけ使う。中身は見せない）
  async fetch(_req, env) {
    try {
      const l = await check(env);
      return new Response(JSON.stringify(l), { headers: { "content-type": "application/json" } });
    } catch (e) {
      return new Response("error", { status: 500 });
    }
  },
};
