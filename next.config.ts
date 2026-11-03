import fs from "node:fs";
import type { NextConfig } from "next";

/**
 * 製品のまとめページ（content/products）ができた製品の古い製品ガイドは、まとめページへ恒久的に転送する。
 * 同じ検索語を2ページで取り合わないため。ガイドがすべて移ったら一覧も転送する
 */
function guideRedirects() {
  try {
    const names = (dir: string) => fs.readdirSync(dir).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5));
    // 公式の料金がつながった製品（content/product_sources.json）だけ。中身の薄いまとめページへは転送しない（lib/products.ts の supersedesGuide と同じ）
    const linked = JSON.parse(fs.readFileSync("content/product_sources.json", "utf8")).items as Record<string, { pricing?: unknown }>;
    const products = new Set(names("content/products").filter((slug) => linked[slug]?.pricing));
    const guides = names("content/guides");
    const moved = guides.filter((slug) => products.has(slug));
    // 使い方ガイド（content/howto）があれば一覧（/guides）は残す
    const howtos = fs.existsSync("content/howto") ? names("content/howto") : [];
    return [
      // 計算ツールの一覧は、ツールが防衛特別所得税だけになったので、そのページへ転送する（2026-11-03 台帳 W12）
      { source: "/tools", destination: "/tools/defense-tax", permanent: true },
      ...Object.entries(MERGED_GUIDES).map(([from, to]) => ({ source: `/guides/${from}`, destination: `/guides/${to}`, permanent: true })),
      ...moved.map((slug) => ({ source: `/guides/${slug}`, destination: `/products/${slug}`, permanent: true })),
      ...(howtos.length === 0 && guides.length > 0 && moved.length === guides.length
        ? [{ source: "/guides", destination: "/products", permanent: true }]
        : []),
    ];
  } catch {
    return [];
  }
}

/**
 * 1つにまとめた使い方ガイド（古い URL → まとめた先）。2026-11-03 オーナー承認:
 * 「Meta Muse」「Muse Wonder」「Museとdot」の3つの見出し・ページを1つのガイドにした（台帳 W15）
 */
const MERGED_GUIDES: Record<string, string> = {
  "meta-muse-wonder": "meta-muse-japan-availability",
  "meta-muse-openai-dot-compare": "meta-muse-japan-availability",
};

// Cloudflare Workers 向けビルド（scripts/cf_build.sh が CF_BUILD=1 を立てる）
const isCloudflare = process.env.CF_BUILD === "1";

const nextConfig: NextConfig = {
  // Workers では sharp（ネイティブ）が動かないので代役に差し替える（lib/sharp-cf.ts）
  ...(isCloudflare ? { turbopack: { resolveAlias: { sharp: "./lib/sharp-cf.ts" } } } : {}),
  images: {
    // Vercel の画像最適化は無料プランの月間上限があり、超えると画像が出なくなる（2026-10 に発生）。
    // YouTube のサムネイルも独自カバーも配信時点で十分軽いので、最適化を使わずそのまま配信する
    unoptimized: true,
    remotePatterns: [
      { protocol: "https", hostname: "i.ytimg.com" },
      { protocol: "https", hostname: "img.youtube.com" },
      { protocol: "https", hostname: "yt3.ggpht.com" },
    ],
  },
  // 独自ドメインへの引っ越し。旧URLとwwwは恒久的（301相当）に新ドメインへ転送し、
  // 検索エンジンの評価と外部リンクを引き継ぐ。プレビュー用の *.vercel.app は対象外
  // public/.well-known はデプロイに載らないため、ルートで返す
  async rewrites() {
    return [{ source: "/.well-known/atproto-did", destination: "/api/atproto-did" }];
  },
  async redirects() {
    return [
      ...["ai-news-auto.vercel.app", "www.ai-tech-express.jp"].map((host) => ({
        source: "/:path*",
        has: [{ type: "host" as const, value: host }],
        destination: "https://ai-tech-express.jp/:path*",
        permanent: true,
      })),
      ...guideRedirects(),
    ];
  },
  // 新しい記事・カバーだけビルド時に静的化し、残りは初回アクセス時に生成する（ISR）。
  // そのとき JSON・フォント・設定ファイルをサーバー関数から読むので、必ず同梱させる
  // （同梱されないと本番で古い記事が 404 になる）。
  // 記事の本文（content/articles、記事数に比例して増える）は1本ずつ読むページにだけ同梱し、
  // 一覧・タグ・サイトマップなどには軽い索引（content/index.json、npm run build の最初に作る）だけを渡す。
  // 全ページに本文を同梱すると、デプロイのたびに容量（Deployment Storage）が膨らむため。
  // キーは picomatch のパターンなので、[slug] の角かっこはエスケープする
  outputFileTracingIncludes: {
    "/**": [
      "./content/index.json",
      "./content/guides/**/*.json",
      "./content/howto/**/*.json",
      "./content/products/**/*.json",
      "./content/buying-guides/**/*.json",
      "./content/digests/**/*.json",
      "./content/offers.json",
      "./content/products.json",
      "./content/state.json",
      "./affiliate_links.json",
      "./monetization.json",
    ],
    "/articles/\\[slug\\]": ["./content/articles/**/*.json"],
    "/cover/**": ["./content/articles/**/*.json", "./assets/fonts/**/*"],
    "/card/**": ["./content/articles/**/*.json", "./assets/fonts/**/*"],
    "/api/v1/articles": ["./content/articles/**/*.json"],
  },
  experimental: {
    optimizePackageImports: ["marked"],
    // 記事カードから記事ページへの画面遷移（React の ViewTransition）は Next 16.4 から標準で有効
  },
};

// next dev で Workers のバインディング（KV など）を使うときだけ: CF_DEV=1 npx next dev -p 3100
if (process.env.CF_DEV === "1") {
  import("@opennextjs/cloudflare").then((m) => m.initOpenNextCloudflareForDev());
}

export default nextConfig;
