import { readdirSync, readFileSync } from "@/lib/content-fs";
import { getArticleSummary, type ArticleSummary } from "@/lib/articles";
import { COMPANIES, companyOf, type Company } from "@/lib/companies";
import { easyGist } from "@/lib/general";

/**
 * 朝のまとめ「今朝のAIニュース3本」の号（edition）。
 * 号のデータは scripts/digest_page.py（morning_digest.py から呼ぶ）が作る:
 *   - content/digests/<YYYYMMDD>.json … git に残り、次のデプロイからサイトに同梱される
 *   - Workers KV（IMAGES_KV）の digest-<YYYYMMDD>.json … 朝 7:05 に作ってすぐ（デプロイ前）でも読めるように
 * 記事の中身（写真・要約）は一覧用の索引（content/index.json）から引くので、本文の JSON は読まない。
 */
export type DigestItemData = { slug: string; headline: string };
export type DigestData = {
  id: string;
  date: string;
  heading: string;
  createdAt: string;
  items: DigestItemData[];
  /** 案 C の背景（/img/<名前>.jpg）。無ければ写真で描く */
  aiImage?: string;
};

export type DigestStory = {
  slug: string;
  headline: string;
  summary: string;
  photo: string;
  credit: string;
  company: Company;
  article: ArticleSummary;
};

export type Digest = DigestData & { stories: DigestStory[] };

const ID = /^\d{8}$/;
const DIR = "content/digests";

function valid(value: unknown): value is DigestData {
  const d = value as DigestData;
  return Boolean(d && ID.test(d.id) && Array.isArray(d.items) && d.items.every((i) => typeof i?.slug === "string" && typeof i?.headline === "string"));
}

/** 同梱されている号の ID（新しい順） */
export function bundledDigestIds(): string[] {
  try {
    return readdirSync(`${process.cwd()}/${DIR}`)
      .filter((name) => /^\d{8}\.json$/.test(name))
      .map((name) => name.slice(0, 8))
      .sort()
      .reverse();
  } catch {
    return [];
  }
}

function readBundled(id: string): DigestData | null {
  try {
    const parsed: unknown = JSON.parse(readFileSync(`${process.cwd()}/${DIR}/${id}.json`, "utf8"));
    return valid(parsed) && parsed.id === id ? parsed : null;
  } catch {
    return null;
  }
}

type KvLike = { get(key: string, type: "text"): Promise<string | null> };

/** Cloudflare で動いているときだけ KV を読む（ビルド時・手元では null） */
async function readKv(id: string): Promise<DigestData | null> {
  if (!(globalThis as { __SAISOKU_VFS__?: unknown }).__SAISOKU_VFS__) return null;
  try {
    const { getCloudflareContext } = await import("@opennextjs/cloudflare");
    const { env } = await getCloudflareContext({ async: true });
    const kv = (env as unknown as { IMAGES_KV?: KvLike }).IMAGES_KV;
    const text = kv ? await kv.get(`digest-${id}.json`, "text") : null;
    const parsed: unknown = text ? JSON.parse(text) : null;
    return valid(parsed) && parsed.id === id ? parsed : null;
  } catch (error) {
    console.error(`[digest] KV read failed: ${id}`, error);
    return null;
  }
}

/** 「要約すると」の1行（かんたん版の「ひとことで言うと」→ 要点 → リード文の最初の1文） */
export function oneLineSummary(article: ArticleSummary, max = 90): string {
  const sources = [easyGist(article), article.highlights?.[0] ?? "", article.lede ?? ""];
  for (const source of sources) {
    const text = (source || "").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    const first = text.match(/^.+?[。！？](?=\s|$|[^」』）)])/)?.[0] ?? text;
    if (first.length <= max) return first;
    const cut = Math.max(first.lastIndexOf("、", max - 1), 0);
    return `${first.slice(0, cut > max * 0.5 ? cut : max - 1)}…`;
  }
  return "";
}

/** 見出しに出てくる企業（無ければ記事の分類） */
function companyFor(headline: string, article: ArticleSummary): Company {
  return COMPANIES.find((c) => c.pattern.test(headline)) ?? companyOf(article);
}

function creditOf(article: ArticleSummary): string {
  const c = article.imageCredit;
  if (c?.kind === "ai") return "画像はイメージ（AI生成）";
  if (c?.kind === "stock") return "写真はイメージ";
  if (c?.kind === "official") return `画像：${c.org || c.via || c.provider || "公式発表"}`;
  return "";
}

export function toDigest(data: DigestData): Digest {
  const stories: DigestStory[] = [];
  for (const item of data.items) {
    const article = getArticleSummary(item.slug);
    if (!article) continue;
    stories.push({
      slug: item.slug,
      headline: item.headline || article.title,
      summary: oneLineSummary(article),
      photo: article.thumbnail || "",
      credit: creditOf(article),
      company: companyFor(item.headline, article),
      article,
    });
  }
  return { ...data, stories };
}

/** 号を読む（同梱 → KV）。記事が2本未満しか見つからなければ null */
export async function getDigest(id: string): Promise<Digest | null> {
  if (!ID.test(id)) return null;
  const data = readBundled(id) ?? (await readKv(id));
  if (!data) return null;
  const digest = toDigest(data);
  return digest.stories.length >= 2 ? digest : null;
}

/** 「2026年11月2日（月）」 */
export function digestDateLabel(digest: Pick<DigestData, "date">, withWeekday = true): string {
  const [y, m, d] = digest.date.split("-").map(Number);
  const weekday = "日月火水木金土"[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${y}年${m}月${d}日${withWeekday ? `（${weekday}）` : ""}`;
}

/** 「2026/11/02」（SNS の本文と同じ書き方） */
export function digestSlashDate(digest: Pick<DigestData, "date">): string {
  return digest.date.replaceAll("-", "/");
}

export const digestPath = (id: string) => `/digest/${id}`;
export const digestImagePath = (id: string) => `/digest/${id}/share.png`;
export const digestCardPath = (id: string) => `/digest/${id}/card.png`;
