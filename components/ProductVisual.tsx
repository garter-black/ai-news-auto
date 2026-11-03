import { getCompany, OTHER } from "@/lib/companies";
import type { BrandImage, ProductSource } from "@/lib/product-substance";

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
        {/* 公式プレスキットの画像は自前の KV（/img/brand-<名前>）に置いたものだけ（外部への直リンクはしない） */}
        <BrandImg image={image} alt="" width={size} height={size} className="size-full object-contain" />
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

/**
 * 公式のロゴ画像。暗い画面用の公式の版（srcDark）があれば、明るい画面は src・暗い画面は srcDark を出す
 * （OpenAI のガイドライン: 明るい背景に Black、暗い背景に White。色の塗り替えはしない）。切り替えは app/globals.css の .brand-on-light / .brand-on-dark
 */
export function BrandImg({ image, alt, width, height, className = "" }: { image: BrandImage; alt: string; width: number; height: number; className?: string }) {
  if (!image.srcDark) return <img src={image.src} alt={alt} width={width} height={height} className={className} loading="lazy" decoding="async" />;
  return (
    <>
      {/* 両方とも先に読む（数KB）。画面の明暗を切り替えた直後にロゴが空にならないように */}
      <img src={image.src} alt={alt} width={width} height={height} className={`brand-on-light ${className}`} decoding="async" />
      <img src={image.srcDark} alt={alt} width={width} height={height} className={`brand-on-dark ${className}`} decoding="async" />
    </>
  );
}
