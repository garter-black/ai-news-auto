import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import AssetShell, { Crumbs } from "@/components/assets/AssetShell";
import ShareLinks from "@/components/assets/ShareLinks";
import ImageCredit from "@/components/ImageCredit";
import Thumb from "@/components/Thumb";
import { sourceLabel } from "@/lib/articles";
import {
  bundledDigestIds,
  digestDateLabel,
  digestImagePath,
  digestPath,
  getDigest,
  type Digest,
  type DigestStory,
} from "@/lib/digest";
import { siteConfig } from "@/site.config";

/**
 * 朝のまとめ「今朝のAIニュース3本」の1号（SNS の朝の投稿のリンク先）。
 * 号のデータは同梱分（content/digests）か、デプロイ前の朝は KV（lib/digest.ts）。KV を読むので静的化しない。
 * 中身は各記事への入口だけ（1本3行）なので検索には出さない（noindex, follow）。シェア画像は /digest/<id>/share.png。
 */
export const dynamic = "force-dynamic";

type Props = { params: Promise<{ id: string }> };

function titleOf(digest: Digest): string {
  return `${digest.heading.replace(/\d+本$/, `${digest.stories.length}本`)}（${digestDateLabel(digest, false)}）`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const digest = await getDigest(id);
  if (!digest) return { robots: { index: false, follow: true } };
  const title = titleOf(digest);
  const description = digest.stories.map((s) => s.headline).join("／");
  const image = { url: digestImagePath(digest.id), width: 1200, height: 630, alt: title };
  return {
    title,
    description,
    alternates: { canonical: digestPath(digest.id) },
    robots: { index: false, follow: true },
    openGraph: { type: "article", url: digestPath(digest.id), title, description, publishedTime: digest.createdAt, images: [image] },
    twitter: { card: "summary_large_image", title, description, images: [image.url] },
  };
}

export default async function DigestPage({ params }: Props) {
  const { id } = await params;
  const digest = await getDigest(id);
  if (!digest) notFound();

  const title = titleOf(digest);
  const url = `${siteConfig.url}${digestPath(digest.id)}`;
  const ids = bundledDigestIds();
  const older = ids.find((other) => other < digest.id);
  const newer = [...ids].reverse().find((other) => other > digest.id);

  return (
    <AssetShell>
      <Crumbs items={[{ href: "/", label: "ホーム" }, { href: "/digest", label: "朝のAIニュース" }, { label: digestDateLabel(digest, false) }]} />

      <header className="mt-5">
        <p className="text-meta font-bold text-muted">毎朝7時30分 ・ 昨夜から今朝までのAIニュース</p>
        <h1 className="t-h1 headline mt-2">{title}</h1>
        <p className="mt-3 text-desc leading-[1.85] text-ink-soft">
          忙しい朝でも押さえておきたいAIのニュースを{digest.stories.length}本だけ選びました。気になった1本から読めます。
        </p>
      </header>

      <ol className="mt-8 grid gap-6">
        {digest.stories.map((story, index) => (
          <Story key={story.slug} story={story} rank={index + 1} priority={index === 0} />
        ))}
      </ol>

      <div className="mt-12 grid gap-4 border-t border-line pt-6">
        <p className="text-desc text-ink">
          ほかの新着は{" "}
          <Link href="/news" className="font-bold underline underline-offset-2 hover:text-ink">
            ニュース一覧
          </Link>{" "}
          から読めます。
        </p>
        <ShareLinks url={url} title={title} />
        <nav aria-label="ほかの日" className="flex flex-wrap justify-between gap-3 text-note">
          {older ? (
            <Link href={digestPath(older)} className="font-bold text-ink-soft hover:text-ink hover:underline">
              ← 前の日
            </Link>
          ) : (
            <span />
          )}
          {newer ? (
            <Link href={digestPath(newer)} className="font-bold text-ink-soft hover:text-ink hover:underline">
              次の日 →
            </Link>
          ) : (
            <Link href="/digest" className="font-bold text-ink-soft hover:text-ink hover:underline">
              これまでの朝のまとめ →
            </Link>
          )}
        </nav>
      </div>
    </AssetShell>
  );
}

function Story({ story, rank, priority }: { story: DigestStory; rank: number; priority: boolean }) {
  const href = `/articles/${story.slug}`;
  const color = story.company.slug === "other" ? "#c8221a" : story.company.color;
  return (
    <li className="overflow-hidden rounded-xl border border-line bg-surface">
      <Link href={href} className="relative block aspect-video bg-surface-2" aria-hidden tabIndex={-1}>
        <Thumb src={story.photo} alt="" fill sizes="(min-width: 860px) 812px, 100vw" className="object-cover" priority={priority} />
        <span className="absolute left-3 top-3 grid h-9 w-9 place-items-center rounded-full bg-white text-[1.05rem] font-black text-ink shadow">
          {rank}
        </span>
      </Link>
      <div className="p-4 sm:p-5" style={{ borderLeft: `4px solid ${color}` }}>
        <p className="flex flex-wrap items-center gap-x-2 text-meta text-muted">
          {story.company.slug !== "other" && <span className="font-bold text-ink">{story.company.name}</span>}
          <span>出典: {sourceLabel(story.article)}</span>
        </p>
        <h2 className="mt-1.5 text-[1.25rem] font-bold leading-snug text-ink sm:text-[1.35rem]">
          <Link href={href} className="hover:underline">
            {story.headline}
          </Link>
        </h2>
        {story.summary && (
          <p className="mt-3 text-desc leading-[1.85] text-ink">
            <span className="mr-2 rounded bg-surface-2 px-1.5 py-0.5 text-meta font-bold text-ink-soft">要約すると</span>
            {story.summary}
          </p>
        )}
        <p className="mt-3">
          <Link href={href} className="text-desc font-bold text-accent underline underline-offset-2 hover:text-ink">
            記事を読む
          </Link>
        </p>
        <ImageCredit credit={story.article.imageCredit} className="mt-2" />
      </div>
    </li>
  );
}
