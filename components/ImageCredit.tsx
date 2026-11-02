import type { ImageCredit as Credit } from "@/lib/articles";
import { figureCreditText, officialCreditText } from "@/lib/image-credit";

/**
 * 写真の下に出す注記。フリー素材は作者・出典・ライセンスへのリンク（CC BY の表示義務）、
 * AI 生成は生成画像であることを明記する。公式発表の写真（official。縮小して自前で配信）は発表した会社・団体の名前。
 * 元記事の写真への直リンク（source。旧方式）は出典欄があるので何も出さない
 */
export default function ImageCredit({ credit, className = "" }: { credit?: Credit; className?: string }) {
  if (!credit || credit.kind === "source") return null;
  const base = `text-note leading-relaxed text-ink-soft ${className}`;
  if (credit.kind === "official") {
    const text = officialCreditText(credit);
    return text ? <p className={base}>{text}</p> : null;
  }
  if (credit.kind === "figure") {
    const text = figureCreditText(credit);
    return text ? <p className={base}>{text}</p> : null;
  }
  if (credit.kind === "ai") {
    return <p className={base}>画像はイメージ（AI生成）です。記事の内容を写したものではありません。</p>;
  }
  const link = "underline decoration-line-strong underline-offset-2 hover:text-ink";
  return (
    <p className={base}>
      写真はイメージです。写真:{" "}
      {credit.creatorUrl ? (
        <a href={credit.creatorUrl} target="_blank" rel="noopener nofollow" className={link}>
          {credit.creator}
        </a>
      ) : (
        credit.creator
      )}
      {credit.provider && (
        <>
          {" / "}
          {credit.sourceUrl ? (
            <a href={credit.sourceUrl} target="_blank" rel="noopener nofollow" className={link}>
              {credit.provider}
            </a>
          ) : (
            credit.provider
          )}
        </>
      )}
      {credit.license && (
        <>
          {"（"}
          {credit.licenseUrl ? (
            <a href={credit.licenseUrl} target="_blank" rel="noopener nofollow license" className={link}>
              {credit.license}
            </a>
          ) : (
            credit.license
          )}
          {credit.license.startsWith("CC BY") ? "・トリミング" : ""}
          {"）"}
        </>
      )}
    </p>
  );
}
