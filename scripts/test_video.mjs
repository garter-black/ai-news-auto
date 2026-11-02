import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// cloudflare/video.ts（/video/ の配信と、動画投稿からの訪問の数え方）のテスト。本番の KV には通信しない
const dir = await mkdtemp(join(tmpdir(), "saisoku-video-test-"));
try {
  const outfile = join(dir, "test.mjs");
  await build({ entryPoints: ["cloudflare/video.test.ts"], outfile, bundle: true, platform: "node", format: "esm", logLevel: "warning" });
  await import(pathToFileURL(outfile).href);
} finally {
  await rm(dir, { recursive: true, force: true });
}
