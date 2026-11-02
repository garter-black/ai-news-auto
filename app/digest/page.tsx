import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import Link from "next/link";
import AssetShell, { Crumbs, QHeading } from "@/components/assets/AssetShell";
import { bundledDigestIds, digestDateLabel, digestPath, getDigest } from "@/lib/digest";

/** 朝のまとめ「今朝のAIニュース3本」の一覧（新しい順）。各号への入口だけなので検索には出さない */
export const dynamic = "force-static";
export const revalidate = 3600;

export const metadata: Metadata = withPageShare("digest", "/digest", {
  title: "朝のAIニュース（毎朝3本）",
  description: "毎朝7時30分に、昨夜から今朝までのAIニュースを3本だけ選んでまとめています。",
  alternates: { canonical: "/digest" },
  robots: { index: false, follow: true },
});

export default async function DigestIndex() {
  const digests = (await Promise.all(bundledDigestIds().slice(0, 60).map((id) => getDigest(id)))).filter((d) => d !== null);
  return (
    <AssetShell>
      <Crumbs items={[{ href: "/", label: "ホーム" }, { label: "朝のAIニュース" }]} />
      <header className="mt-5">
        <h1 className="t-h1 headline">朝のAIニュース（毎朝3本）</h1>
        <p className="mt-3 text-desc leading-[1.85] text-ink-soft">毎朝7時30分に、昨夜から今朝までのAIニュースを3本だけ選んでいます。</p>
      </header>
      <section aria-labelledby="list-heading" className="mt-10">
        <QHeading id="list-heading">これまでの朝のまとめは？</QHeading>
        <ul className="grid gap-3">
          {digests.map((digest) => (
            <li key={digest.id}>
              <Link href={digestPath(digest.id)} className="group block rounded-lg border border-line px-4 py-3 hover:border-ink">
                <span className="text-meta font-bold tabular-nums text-muted">{digestDateLabel(digest)}</span>
                <span className="mt-1 grid gap-0.5">
                  {digest.stories.map((story) => (
                    <span key={story.slug} className="text-[15px] font-bold leading-snug text-ink group-hover:underline">
                      ・{story.headline}
                    </span>
                  ))}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </AssetShell>
  );
}
