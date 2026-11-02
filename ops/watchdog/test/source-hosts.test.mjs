// 速報の発信元のホストがレーダーの公式ホストにも入るか（一覧の食い違いで「対象外のURL」にしない）
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { mergeRegistry } from "../src/fastlane.js";
import { compileRadar, officialOf } from "../src/radar.js";

const rules = JSON.parse(readFileSync(new URL("../../../scripts/fast_lane_rules.json", import.meta.url), "utf-8"));
const registry = JSON.parse(readFileSync(new URL("../../../scripts/sources_registry.json", import.meta.url), "utf-8"));

test("登録済みの発信元のURLは公式として扱う", () => {
  const RR = compileRadar(mergeRegistry(rules, registry));
  assert.ok(officialOf("https://elevenlabs.io/blog/new-voice-model-launch", [], RR));
  assert.ok(officialOf("https://www.redhat.com/en/blog/red-hat-ai-new-release", [], RR));
  assert.equal(officialOf("https://prtimes.jp/main/html/rd/p/000000001.000000001.html", [], RR), null);
});
