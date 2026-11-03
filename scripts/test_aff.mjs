import { build } from "esbuild";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const dir = await mkdtemp(join(tmpdir(), "saisoku-aff-test-"));
try {
  const outfile = join(dir, "test.mjs");
  await build({ entryPoints: ["cloudflare/aff.test.ts"], outfile, bundle: true, platform: "node", format: "esm", logLevel: "warning" });
  await import(pathToFileURL(outfile).href);
  const inflowOutfile = join(dir, "inflow-test.mjs");
  await build({ entryPoints: ["cloudflare/inflow.test.ts"], outfile: inflowOutfile, bundle: true, platform: "node", format: "esm", logLevel: "warning" });
  await import(pathToFileURL(inflowOutfile).href);
  const reviewOutfile = join(dir, "review-test.mjs");
  await build({ entryPoints: ["cloudflare/review.test.ts"], outfile: reviewOutfile, bundle: true, platform: "node", format: "esm", logLevel: "warning" });
  await import(pathToFileURL(reviewOutfile).href);
  const clientOutfile = join(dir, "client-test.mjs");
  await build({ entryPoints: ["components/RevenueFunnelTracker.test.ts"], outfile: clientOutfile, bundle: true, platform: "node", format: "esm", logLevel: "warning", plugins: [{
    name: "unused-react-hooks-for-client-event-unit-test",
    setup(build) {
      build.onResolve({ filter: /^(react|next\/navigation)$/ }, args => ({ path: args.path, namespace: "test-hooks" }));
      build.onLoad({ filter: /.*/, namespace: "test-hooks" }, () => ({ contents: "export const useEffect=()=>{}; export const useRef=()=>({current:null}); export const usePathname=()=>null;", loader: "js" }));
    },
  }] });
  await import(pathToFileURL(clientOutfile).href);
  const attributionOutfile = join(dir, "attribution-test.mjs");
  await build({ entryPoints: ["lib/a8-attribution.test.ts"], outfile: attributionOutfile, bundle: true, platform: "node", format: "esm", logLevel: "warning" });
  await import(pathToFileURL(attributionOutfile).href);
  const footerOutfile = join(dir, "footer-link-test.mjs");
  await build({ entryPoints: ["components/DifyDecisionLink.test.tsx"], outfile: footerOutfile, bundle: true, platform: "node", format: "esm", logLevel: "warning", plugins: [{
    name: "next-pathname-for-footer-link-test",
    setup(build) {
      build.onResolve({ filter: /^next\/(navigation|link)$/ }, args => ({ path: args.path, namespace: "test-next-link" }));
      build.onLoad({ filter: /.*/, namespace: "test-next-link" }, () => ({ contents: "export const usePathname=()=>globalThis.__testDifyPathname; export default function Link(){}", loader: "js" }));
    },
  }] });
  await import(pathToFileURL(footerOutfile).href);
} finally {
  await rm(dir, { recursive: true, force: true });
}
