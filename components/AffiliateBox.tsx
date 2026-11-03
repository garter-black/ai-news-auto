import type { AffiliateLink } from "@/lib/articles";

type Props = {
  links: AffiliateLink[];
  compact?: boolean;
};

/**
 * 記事内で言及されたツールのアフィリエイトリンク。
 * Google の品質ガイドラインに従い rel="sponsored" を必ず付与し、PR表記を併記する。
 */
export default function AffiliateBox({ links, compact }: Props) {
  if (links.length === 0) return null;

  return (
    <section
      data-aff-pos="box"
      className={`rounded-2xl border border-line bg-surface ${compact ? "p-5" : "p-6"}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h2 className={compact ? "t-card" : "t-h3"}>
          この記事に関連するツール・サービス
        </h2>
        <span className="shrink-0 rounded bg-line px-1.5 py-0.5 text-meta text-muted">
          PR
        </span>
      </div>

      <ul className={`mt-4 ${compact ? "space-y-2.5" : "space-y-3"}`}>
        {links.map((link) => (
          <li key={link.url}>
            <a
              href={link.url}
              target="_blank"
              rel="sponsored nofollow noopener noreferrer"
              className="group flex items-start gap-3 rounded-xl border border-line px-4 py-3 transition-colors hover:border-accent hover:bg-accent-soft/40"
            >
              <span className="min-w-0">
                <span className="block text-note font-semibold text-ink group-hover:text-accent">
                  {link.label}
                </span>
                {link.note && (
                  <span className="mt-0.5 block text-note leading-relaxed text-muted">
                    {link.note}
                  </span>
                )}
              </span>
            </a>
            {link.pixel && !compact && process.env.VERCEL_ENV === "production" && (
              // ASPの表示回数計測（A8.net 推奨）。見た目には影響しない
              // eslint-disable-next-line @next/next/no-img-element
              <img src={link.pixel} width={1} height={1} alt="" loading="lazy" fetchPriority="low" className="absolute size-px opacity-0" />
            )}
          </li>
        ))}
      </ul>

      <p className="mt-4 text-note leading-relaxed text-muted">
        ※ 上記リンクはアフィリエイトリンクを含みます。リンク経由で申し込まれた場合、当サイトに紹介料が支払われることがあります。価格・提供条件は各公式サイトをご確認ください。
      </p>
    </section>
  );
}
