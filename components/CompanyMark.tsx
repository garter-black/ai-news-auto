import type { Company } from "@/lib/companies";

type Props = {
  company: Company;
  /** 表示サイズ（px） */
  size?: number;
  /** 企業名も並べて出す */
  withName?: boolean;
  className?: string;
};

/**
 * 企業の色見本。ロゴは使わず、企業ごとの色と頭文字だけで見分けられるようにする。
 * 名前を並べる場合、色見本は装飾なので読み上げない。
 */
export default function CompanyMark({ company, size = 20, withName, className = "" }: Props) {
  const swatch = (
    <span
      aria-hidden={withName ? true : undefined}
      aria-label={withName ? undefined : company.name}
      role={withName ? undefined : "img"}
      // 企業の色見本（ロゴ扱い）。文字サイズ・コントラストのルールの対象外（docs/typography.md）
      data-decorative=""
      className="index-num inline-grid shrink-0 place-items-center rounded-[6px] font-semibold leading-none text-white"
      style={{
        width: size,
        height: size,
        background: company.color,
        fontSize: Math.round(size * (company.mark.length > 1 ? 0.42 : 0.55)),
      }}
    >
      {company.mark}
    </span>
  );

  if (!withName) return <span className={className}>{swatch}</span>;

  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      {swatch}
      <span className="font-bold text-ink">{company.name}</span>
    </span>
  );
}

/**
 * 記事の「発信元の企業」ラベル。決まった企業（OpenAI など）に当たらない記事は「その他」ではなく
 * ジャンル名（投資・マネー、社会・行政 など）を出す。ビジネス・お金の記事の半分近くが「その他」と出ていたため（2026-11-03）。
 */
export function ArticleMark({ company, genre, size = 20, className = "" }: { company: Company; genre?: string; size?: number; className?: string }) {
  if (company.slug === "other" && genre) {
    return <span className={`font-bold text-ink ${className}`}>{genre}</span>;
  }
  return <CompanyMark company={company} size={size} withName className={className} />;
}
