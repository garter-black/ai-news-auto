import type { OfferItem } from "@/lib/offers";
import { priceIsFresh } from "@/lib/offer-time";

const yen = new Intl.NumberFormat("ja-JP");
const dateFormat = new Intl.DateTimeFormat("ja-JP", { month: "numeric", day: "numeric", timeZone: "Asia/Tokyo" });

export function priceText(item: OfferItem): string | undefined {
  if (item.price != null) return `¥${yen.format(item.price)}`;
  return item.priceText;
}

/** 商品カタログのみ、確認後48時間を超えた金額は表示しない。 */
export function catalogPriceText(item: OfferItem, now: number | null): string | undefined {
  if ((item.price != null || item.priceText) && !priceIsFresh(item.priceFreshUntil, now)) return "最新価格を確認";
  return priceText(item);
}

export function checkedDate(iso: string): string {
  return dateFormat.format(Date.parse(iso));
}

/** 楽天の売れ筋で上位のときだけ出す実データのバッジ */
export function rankBadge(item: OfferItem): string | undefined {
  if (item.kind !== "book" || !item.rank || item.rank > 10) return undefined;
  return `楽天売れ筋${item.rank}位`;
}

/** 1位に添える「ここがおすすめ」。提携商品は編集部の一言、本は楽天の実データから作る */
export function reasonOf(item: OfferItem): string | undefined {
  if (item.note) return item.note;
  if (item.kind !== "book") return undefined;
  const parts: string[] = [];
  if (item.rank && item.rank <= 10 && item.keyword) parts.push(`楽天ブックス「${item.keyword}」の売れ筋${item.rank}位`);
  if (item.rating != null && item.reviews && item.reviews >= 3) parts.push(`レビュー${item.reviews}件で平均${item.rating.toFixed(1)}`);
  return parts.length > 0 ? parts.join("。") : undefined;
}

export function Stars({ value, size = 12 }: { value: number; size?: number }) {
  const percent = Math.max(0, Math.min(100, (value / 5) * 100));
  return (
    <span
      aria-label={`5点中${value.toFixed(1)}点`}
      className="relative inline-block leading-none tracking-[1px]"
      style={{ fontSize: size }}
    >
      <span className="text-line-strong">★★★★★</span>
      <span className="absolute inset-0 overflow-hidden whitespace-nowrap text-[#e8a33d]" style={{ width: `${percent}%` }}>
        ★★★★★
      </span>
    </span>
  );
}

export function Rating({ item, size = 12 }: { item: OfferItem; size?: number }) {
  if (item.rating == null || !item.reviews) return null;
  return (
    <span className="flex items-center gap-1 text-meta text-ink-soft">
      <Stars value={item.rating} size={size} />
      <span className="index-num font-semibold">{item.rating.toFixed(1)}</span>
      <span className="index-num text-muted">（{item.reviews}件）</span>
    </span>
  );
}

/** 商品写真。白地で見せる（ダーク表示でも商品の色が正しく見えるように） */
export function ProductImage({
  item,
  className = "",
  pad = "p-4",
  eager = false,
  compact = false,
}: {
  item: OfferItem;
  className?: string;
  pad?: string;
  /** 最初から画面に見える位置（右上の1位など）は遅延読み込みしない */
  eager?: boolean;
  /** 小さな枠（72px の2〜3位など）。写真が無いときは頭文字の札だけを出す */
  compact?: boolean;
}) {
  return (
    <span className={`relative grid place-items-center overflow-hidden bg-white ${className}`}>
      {item.image ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={item.image} alt="" loading={eager ? "eager" : "lazy"} className={`absolute inset-0 h-full w-full object-contain ${pad}`} />
      ) : compact ? (
        <span aria-hidden="true" className="grid size-full place-items-center bg-[#f3f5f8]">
          <span className="grid size-10 place-items-center rounded-lg bg-[#1d4a8f] text-[18px] font-black text-white">{initialOf(item.name)}</span>
        </span>
      ) : (
        // 写真の無いサービスは、頭文字の札（サイトの紺）と名前で見せる。「WEB SERVICE」のような意味のない英語の板は出さない
        // （2026-11-03 の点検: 文字だけの板が壊れた画像のように見えた）
        <span className="flex size-full flex-col items-center justify-center gap-2.5 bg-[#f3f5f8] px-4 text-center">
          <span aria-hidden="true" className="grid size-12 place-items-center rounded-xl bg-[#1d4a8f] text-[22px] font-black text-white">
            {initialOf(item.name)}
          </span>
          <span className="text-[16px] font-bold leading-tight text-[#0b1a33] text-balance">{item.name}</span>
          {item.label && <span className="text-badge font-bold text-[#1d4a8f]">{item.label}</span>}
        </span>
      )}
    </span>
  );
}

/** 写真の無いサービスの頭文字（「6週間チャレンジ オフィス英会話」→「6」、「松井証券」→「松」） */
export function initialOf(name: string): string {
  const first = Array.from((name ?? "").trim())[0] ?? "";
  return first.toUpperCase();
}

export function PrNote({ checkedAt, hasBook }: { checkedAt: string; hasBook: boolean }) {
  return (
    <p className="text-note leading-relaxed text-muted">
      {/* 並びは閲覧履歴で変わり、本が見えていない枠もある（セキュリティソフトだけの枠に「書籍の」と出ていた）ので「書籍」とは言わない */}
      {hasBook && <>表示中の価格・評価は取得時点（{checkedDate(checkedAt)}）のものです。</>}
      価格・在庫・利用条件はリンク先でご確認ください。リンク経由の購入で当サイトに紹介料が入ることがあります。
    </p>
  );
}

/**
 * ASPの表示回数計測ピクセル。本番（Vercel の production）でだけ読み込む。
 * 開発中やプレビューの確認で表示回数が水増しされ、レポートが読めなくなるのを防ぐ。
 */
const COUNT_IMPRESSIONS = process.env.VERCEL_ENV === "production";

export function Pixels({ pixels }: { pixels: string[] }) {
  if (!COUNT_IMPRESSIONS) return null;
  return (
    <>
      {pixels.map((pixel) => (
        // 低優先度にして先読み（preload）させない。読み込み自体はすぐ始まるので計測は変わらない
        // eslint-disable-next-line @next/next/no-img-element
        <img key={pixel} src={pixel} width={1} height={1} alt="" loading="lazy" fetchPriority="low" className="absolute size-px opacity-0" />
      ))}
    </>
  );
}
