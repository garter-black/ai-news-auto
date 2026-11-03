/**
 * 広告枠。
 *
 * 本番の AdSense の枠を出すには、GitHub の Variables に広告ユニットの ID を入れて再デプロイする
 * （.github/workflows/deploy_cloudflare.yml が NEXT_PUBLIC_ADSENSE_SLOT_* に渡す。発行者 ID は scripts/cf_build.sh）:
 *   gh variable set ADSENSE_SLOT_ARTICLE_TOP    --body 1234567890
 *   gh variable set ADSENSE_SLOT_ARTICLE_MIDDLE --body 1234567890
 *   gh variable set ADSENSE_SLOT_SIDEBAR        --body 1234567890
 * 検索に出さない記事（lib/indexing.ts）には枠を出さない（app/articles/[slug]/page.tsx の showAds）。
 *
 * 未設定の間、本番では何も表示しない（空の広告枠は読者の邪魔になり、AdSense審査でも
 * 「中身のない枠」と見なされうるため）。開発中だけ位置確認用のダミー枠を出す。
 */

const CLIENT = process.env.NEXT_PUBLIC_ADSENSE_CLIENT ?? "";

const SLOT_IDS: Record<AdPosition, string> = {
  "article-top": process.env.NEXT_PUBLIC_ADSENSE_SLOT_ARTICLE_TOP ?? "",
  "article-middle": process.env.NEXT_PUBLIC_ADSENSE_SLOT_ARTICLE_MIDDLE ?? "",
  sidebar: process.env.NEXT_PUBLIC_ADSENSE_SLOT_SIDEBAR ?? "",
};

export type AdPosition = "article-top" | "article-middle" | "sidebar";

type Props = {
  position: AdPosition;
  className?: string;
};

export default function AdSlot({ position, className }: Props) {
  const slot = SLOT_IDS[position];
  const enabled = Boolean(CLIENT && slot);
  if (!enabled && process.env.NODE_ENV === "production") return null;

  return (
    <aside
      className={className}
      aria-label="広告"
      data-ad-position={position}
    >
      <p className="mb-1 text-meta tracking-widest text-muted">広告</p>

      {enabled ? (
        <>
          <ins
            className="adsbygoogle block"
            style={{ display: "block" }}
            data-ad-client={CLIENT}
            data-ad-slot={slot}
            data-ad-format={position === "sidebar" ? "vertical" : "auto"}
            data-full-width-responsive={position === "sidebar" ? "false" : "true"}
          />
          <script
            dangerouslySetInnerHTML={{
              __html:
                "(adsbygoogle = window.adsbygoogle || []).push({});",
            }}
          />
        </>
      ) : (
        <div className="rounded-2xl border border-dashed border-line-strong bg-surface px-5 py-5 text-center text-meta text-muted">
          AdSense Dummy
        </div>
      )}
    </aside>
  );
}
