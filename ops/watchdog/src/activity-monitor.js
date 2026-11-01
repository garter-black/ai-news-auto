// Watch for a lack of candidate detections, not just successful Actions runs.
// This is observational: it never modifies the breaking-news queue or dispatches posts.
export const ACTIVITY_TITLE = "【自動通知】速報候補の検知が長時間ありません";
const KEY = "activity-monitor";
const WINDOW_MS = 6 * 60 * 60 * 1000;

export async function checkActivity(env, now, gh) {
  const previous = (await env.FAST.get(KEY, "json")) || {};
  const lastCheck = Date.parse(previous.checkedAt || "");
  if (Number.isFinite(lastCheck) && now.getTime() - lastCheck < 9 * 60 * 1000) return { skipped: "interval" };
  const fast = (await env.FAST.get("state", "json")) || {};
  const radar = (await env.FAST.get("radar", "json")) || {};
  const times = [...Object.values(fast.queue || {}), ...Object.values(radar.outbox || {})]
    .map((item) => Date.parse(item.detectedAt || ""))
    .filter((t) => Number.isFinite(t) && t <= now.getTime());
  const old = Date.parse(previous.lastDetectedAt || "");
  if (Number.isFinite(old) && old <= now.getTime()) times.push(old);
  // With no history, start observing now; don't claim the preceding time was empty.
  const last = times.length ? Math.max(...times) : now.getTime();
  const elapsed = now.getTime() - last;
  const result = { lastDetectedAt: new Date(last).toISOString(), hours: Math.floor(elapsed / 3600000) };
  if (elapsed >= WINDOW_MS) {
    const issues = (await gh('/issues?state=open&labels=missed-news&per_page=100')) || [];
    if (!issues.some((i) => i.title === ACTIVITY_TITLE)) {
      await gh('/issues', { method: 'POST', body: JSON.stringify({
        title: ACTIVITY_TITLE, labels: ['missed-news'],
        body: `速報候補の最後の記録から約${result.hours}時間たっています。\n\n最後の検知記録: ${result.lastDetectedAt}\n\n公式発表が少ない時間帯でも発生します。Workerの取得エラー、レーダー、KVと公式発表を確認してください。これは記事生成や訪問数の停止を断定する通知ではありません。`,
      }) });
      result.alert = 'opened';
    } else result.alert = 'already-open';
  }
  await env.FAST.put(KEY, JSON.stringify({ checkedAt: now.toISOString(), lastDetectedAt: result.lastDetectedAt }));
  return result;
}
