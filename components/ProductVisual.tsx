import { getCompany, OTHER } from "@/lib/companies";
import type { ProductSource } from "@/lib/product-substance";

/**
 * 図鑑の製品の画像（2026-11-03 オーナーの指摘。台帳 W13）。
 * 公式の製品画像・ロゴ（content/product_sources.json の image。公式サイトのものだけ・出典つき）があればそれを出し、
 * 無ければ企業の色の、文字の無い図形を出す。記事の写真・フリー素材・AI 画像は使わない（関係のない写真が出ていた）。
 */
export default function ProductVisual({
  company,
  image,
  size = 56,
  className = "",
}: {
  company: string;
  image?: ProductSource["image"];
  size?: number;
  className?: string;
}) {
  if (image) {
    return (
      <span className={`inline-block shrink-0 overflow-hidden rounded-xl border border-line bg-paper ${className}`} style={{ width: size, height: size }}>
        {/* 公式の画像は自前の /brand に置いたものだけ（外部への直リンクはしない） */}
        <img src={image.src} alt="" width={size} height={size} className="size-full object-contain" loading="lazy" decoding="async" />
      </span>
    );
  }
  const found = getCompany(company);
  const color = found && found.slug !== OTHER.slug ? found.color : "#1d4a8f";
  return (
    <span
      aria-hidden
      data-product-visual="placeholder"
      className={`inline-block shrink-0 overflow-hidden rounded-xl ${className}`}
      style={{ width: size, height: size, background: `linear-gradient(135deg, ${color} 0%, #0b1a33 140%)` }}
    >
      <svg viewBox="0 0 56 56" width={size} height={size} focusable="false">
        <circle cx="46" cy="46" r="26" fill="none" stroke="#fff" strokeOpacity="0.22" strokeWidth="2" />
        <circle cx="46" cy="46" r="16" fill="none" stroke="#fff" strokeOpacity="0.32" strokeWidth="2" />
        <circle cx="46" cy="46" r="6" fill="#fff" fillOpacity="0.5" />
        <rect x="10" y="12" width="14" height="3" rx="1.5" fill="#fff" fillOpacity="0.55" />
      </svg>
    </span>
  );
}
