import type { Offer } from "@/lib/offers";
import RankingBody from "./RankingBody";
import { Pixels, PrNote } from "./parts";
import TimedPixels from "./TimedPixels";

/**
 * 「サイソクのおすすめ3選」。記事の文脈に合う商品を番号つきで並べ、1位を大きく推す。
 * wide は本文中（横長）、それ以外は右側の縦長。どちらも中身は同じ。
 */
export default function RailRanking({
  offer,
  wide = false,
  id,
  title,
  countImpression = true,
}: {
  offer: Offer;
  wide?: boolean;
  id?: string;
  /** 見出し。右側と本文中で同じ見出しが並ばないよう、本文中では変える */
  title?: string;
  /** 同じ枠をページ内に2回出すとき、表示の計測を二重にしないよう片方は false にする */
  countImpression?: boolean;
}) {
  // 掲載順は閲覧履歴で変わるため、順位や実機評価に基づく「イチオシ」とは言わない。
  const badge = "紹介";
  const hasBook = offer.items.some((i) => i.kind === "book");

  return (
    <aside id={id} data-aff-pos={wide ? "ranking-inline" : "ranking-rail"} aria-label="PR：広告・紹介" className="relative overflow-hidden rounded-2xl border border-ink bg-paper">
      <Header offer={offer} wide={wide} title={title ?? offer.headline} />

      <RankingBody items={offer.items} visible={3} wide={wide} badge={badge} />

      <div className="border-t border-line bg-surface px-4 py-2.5">
        <PrNote checkedAt={offer.checkedAt} hasBook={hasBook} />
      </div>
      {countImpression && <><Pixels pixels={offer.pixels} /><TimedPixels enabled={process.env.VERCEL_ENV === "production"} pixels={offer.timedPixels ?? []} /></>}
    </aside>
  );
}

function Header({ offer, wide, title }: { offer: Offer; wide: boolean; title: string }) {
  return (
    <div className={`bg-ink text-paper ${wide ? "px-5 py-4 sm:px-6" : "px-4 py-3.5"}`}>
      <p className="flex items-center gap-2 text-meta font-bold opacity-75">
        <span className="rounded border border-current px-1.5 text-meta tracking-wider">PR</span>
        広告・紹介
      </p>
      <p className={`mt-1.5 font-black leading-snug ${wide ? "text-[22px]" : "text-[19px]"}`}>
        {title}
      </p>
      {title !== offer.headline && <p className="mt-1 text-meta leading-snug opacity-85">{offer.headline}</p>}
    </div>
  );
}

