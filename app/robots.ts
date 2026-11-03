import type { MetadataRoute } from "next";
import { siteConfig } from "@/site.config";

export const dynamic = "force-static";

/**
 * 検索エンジンに加え、AI検索（ChatGPT・Claude・Perplexity・Gemini など）のクローラーも明示的に許可する。
 * AIの回答に引用されること（AEO）も流入経路として狙うため。
 */
const AI_CRAWLERS = [
  "GPTBot",
  "OAI-SearchBot",
  "ChatGPT-User",
  "ClaudeBot",
  "Claude-SearchBot",
  "Claude-User",
  "PerplexityBot",
  "Perplexity-User",
  "Google-Extended",
  "Applebot-Extended",
  "Bingbot",
];

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: ["/api/", "/review"] },
      { userAgent: AI_CRAWLERS, allow: "/", disallow: ["/api/", "/review"] },
    ],
    sitemap: [`${siteConfig.url}/sitemap.xml`, `${siteConfig.url}/news-sitemap.xml`],
    host: siteConfig.url,
  };
}
