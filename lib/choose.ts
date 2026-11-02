import fs from "@/lib/content-fs";
import path from "node:path";
import type { ArticleSummary } from "@/lib/articles";
import { isAffiliateUrl } from "@/lib/affiliate";
import { CHOOSE_GUIDES, type ChooseGuide, type ChooseService } from "@/lib/choose-data";
import { MIN_SCORE, relevanceScore } from "@/lib/offers";

/**
 * 選び方ガイド（/choose/<slug>）。
 * 各サービスのリンクは、A8 で提携済みなら提携リンク、未提携なら公式サイトへの普通のリンク。
 * 提携の判定は offers.json・affiliate_links.json（scripts/a8_import.py が更新）を毎回見るので、
 * 新しく提携したら a8_import を流すだけでページを直さずに切り替わる。
 */

type Partner = { program?: string; name?: string; programId?: string; image?: string | null; link?: string; pixel?: string };
type AffEntry = { url?: string; pixel?: string; label?: string };

export type ResolvedService = ChooseService & {
  href: string;
  /** 提携リンクか（rel="sponsored"・計測ピクセル） */
  sponsored: boolean;
  pixel?: string;
};

function readJson<T>(file: string): T | null {
  const full = path.join(/*turbopackIgnore: true*/ process.cwd(), file);
  if (!fs.existsSync(full)) return null;
  try {
    return JSON.parse(fs.readFileSync(full, "utf8")) as T;
  } catch {
    return null;
  }
}

let loaded: { partners: Partner[]; affiliates: [string, AffEntry][] } | null = null;

function load() {
  if (loaded) return loaded;
  const offers = readJson<{ partners?: Record<string, Partner> }>("content/offers.json");
  const links = readJson<Record<string, unknown>>("affiliate_links.json") ?? {};
  loaded = {
    partners: Object.values(offers?.partners ?? {}),
    affiliates: Object.entries(links).flatMap(([key, value]) =>
      value && typeof value === "object" && !key.startsWith("_") ? [[key, value as AffEntry] as [string, AffEntry]] : [],
    ),
  };
  return loaded;
}

/** 提携リンクを探す：① programId ② A8 バナーの mid ③ 名前 */
function findAffiliate(service: ChooseService): { url: string; pixel?: string } | null {
  const { partners, affiliates } = load();
  const byName = new Map(affiliates);
  const ok = (url?: string) => !!url && isAffiliateUrl(url);
  if (service.programId) {
    const pid = service.programId;
    const partner = partners.find((p) => p.programId === pid || (p.image ?? "").includes(`mid=${pid}`));
    if (partner) {
      const entry = partner.program ? byName.get(partner.program) : undefined;
      const url = partner.link ?? entry?.url;
      if (ok(url)) return { url: url!, pixel: partner.pixel ?? entry?.pixel };
    }
  }
  if (service.names) {
    const pattern = new RegExp(service.names, "i");
    const hit = affiliates.find(([key, entry]) => (pattern.test(key) || pattern.test(entry.label ?? "")) && ok(entry.url));
    if (hit) return { url: hit[1].url!, pixel: hit[1].pixel };
  }
  return null;
}

export function resolveService(service: ChooseService): ResolvedService {
  const aff = findAffiliate(service);
  return aff
    ? { ...service, href: aff.url, sponsored: true, pixel: aff.pixel }
    : { ...service, href: service.officialUrl, sponsored: false };
}

export function getAllChooseGuides(): ChooseGuide[] {
  return CHOOSE_GUIDES;
}

export function getChooseGuide(slug: string): ChooseGuide | undefined {
  return CHOOSE_GUIDES.find((guide) => guide.slug === slug);
}

/**
 * 記事の内容に合う選び方ガイド（見出し・タグに当たり、さらに「あわせて読みたい」と同じ基準点に届くものだけ）。
 * タグ1つ（「料金プラン」など）だけでは出さない。Claude の料金の記事に回線のガイドが出ていた（2026-11-03）
 */
export function chooseGuideForArticle(article: ArticleSummary): ChooseGuide | undefined {
  const text = [article.title, article.seoTitle ?? "", ...article.tags].join(" ");
  return CHOOSE_GUIDES.find(
    (guide) =>
      guide.articleGenres.includes(article.genreSlug) &&
      new RegExp(guide.articleMatch, "i").test(text) &&
      relevanceScore(guide.articleMatch, article) >= MIN_SCORE,
  );
}
