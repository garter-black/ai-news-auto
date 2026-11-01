import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkActivity, ACTIVITY_TITLE } from '../src/activity-monitor.js';

function mock(initial = {}, open = []) {
  const data = structuredClone(initial), calls = [];
  const env = { FAST: { get: async (key) => data[key] || null, put: async (key, value) => { data[key] = JSON.parse(value); } } };
  const gh = async (path, init = {}) => { calls.push([path, init]); return path.startsWith('/issues?') ? open : {}; };
  return { data, calls, env, gh };
}

test('first observation without history does not invent an outage', async () => {
  const m = mock();
  const result = await checkActivity(m.env, new Date('2026-11-01T10:00:00Z'), m.gh);
  assert.equal(result.hours, 0); assert.equal(m.calls.length, 0);
});

test('old candidate produces one issue and never changes queue', async () => {
  const initial = { state: { queue: { item: { detectedAt: '2026-11-01T00:00:00Z', status: 'done' } } } };
  const m = mock(initial);
  const result = await checkActivity(m.env, new Date('2026-11-01T08:00:00Z'), m.gh);
  assert.equal(result.alert, 'opened'); assert.equal(m.calls.length, 2);
  assert.deepEqual(m.data.state, initial.state);
  assert.equal(JSON.parse(m.calls[1][1].body).title, ACTIVITY_TITLE);
});

test('existing alert is not duplicated, radar candidate counts as activity', async () => {
  const old = mock({state:{queue:{a:{detectedAt:'2026-11-01T00:00:00Z'}}}}, [{title:ACTIVITY_TITLE}]);
  assert.equal((await checkActivity(old.env, new Date('2026-11-01T08:00:00Z'), old.gh)).alert, 'already-open');
  assert.equal(old.calls.length, 1);
  const fresh = mock({state:{queue:{a:{detectedAt:'2026-11-01T00:00:00Z'}}},radar:{outbox:{b:{detectedAt:'2026-11-01T07:59:00Z'}}}});
  assert.equal((await checkActivity(fresh.env, new Date('2026-11-01T08:00:00Z'), fresh.gh)).hours, 0);
  assert.equal(fresh.calls.length, 0);
});
