"use client";

import Image, { type ImageProps } from "next/image";
import { useCallback, useState } from "react";
import { preload } from "react-dom";
import type { ThumbSource } from "@/lib/companies";
import { largeThumb, ownSrcSet, ytSrcSet } from "@/lib/thumb-url";

type Stage = "original" | "fallback" | "cover" | "failed";

const YT_JPG = /^https:\/\/i\.ytimg\.com\/vi\/([^/]+)\/([a-z0-9]+)\.jpg$/;

/** YouTube のサムネイルは同じ画質の WebP（JPEG の約3分の1）で配信する */
function ytWebp(src: string): string {
  const m = YT_JPG.exec(src);
  return m ? `https://i.ytimg.com/vi_webp/${m[1]}/${m[2]}.webp` : src;
}

/**
 * 記事のサムネイル。壊れた画像アイコンを読者に見せないための受け皿を2段持つ。
 *
 * 0. YouTube は軽い WebP 版を先に使う（読めなければ 1 の JPEG 標準画質へ）
 * 1. 高解像度版（maxresdefault）は後から消えることがあるので、読み込めなければ標準画質（hqdefault）へ。
 *    サイトの写真（/img/）の一覧用 WebP が読めなければ元の JPEG へ
 * 2. それも読めなければ、記事の独自カバー（/cover/<slug>。cover を渡したときだけ）へ
 * 3. それも無い（元動画が削除・非公開になった）場合や、画像欄が空の場合は、サイト名入りの代替表示へ
 *
 * サーバーで描いた <img> は、React が動き出す前に読み込みに失敗すると onError が届かない（壊れたアイコンのまま残る）。
 * そのため描いた直後に「読み終わったのに幅0＝失敗」を確かめて、同じ受け皿に回す（next/image と同じやり方）。
 *
 * 公式ブログ・プレスリリースの記事（source あり）は、企業色の地に発信元名を載せた代替表示にする。
 *
 * 親要素に relative と大きさ（aspect-video など）が指定されている前提で、fill 画像と同じ位置を埋める。
 */
export default function Thumb({ source, cover, ...props }: ImageProps & { source?: ThumbSource; cover?: string | null }) {
  const original = props.src;
  const empty = typeof original === "string" && original.trim() === "";
  const [stage, setStage] = useState<Stage>(empty ? "failed" : "original");

  if (stage === "failed" && source) {
    return (
      <span
        aria-hidden
        // 写真の代わりの図版。文字の大きさ・コントラストのルールの対象外（docs/typography.md）
        data-decorative=""
        className="@container absolute inset-0 overflow-hidden"
        style={{ background: `linear-gradient(135deg, ${source.color} 0%, color-mix(in srgb, ${source.color} 62%, #000) 100%)` }}
      >
        {source.mark && (
          <span className="absolute -bottom-[14cqw] -right-[4cqw] font-black leading-none tracking-tighter text-white/[0.13] text-[58cqw]">
            {source.mark}
          </span>
        )}
        <span className="absolute left-[6cqw] top-[5.5cqw] font-display text-white/75 text-[max(9px,4.6cqw)]">サイソク</span>
        <span className="absolute inset-x-[6cqw] bottom-[6cqw] flex flex-col gap-[1.6cqw]">
          <span className="w-fit rounded-full bg-white/90 px-[2.4cqw] py-[0.6cqw] font-bold text-ink text-[max(8px,3.4cqw)]">
            公式発表
          </span>
          <span className="line-clamp-2 font-bold leading-[1.3] text-white text-[max(11px,7.4cqw)]">{source.label}</span>
        </span>
      </span>
    );
  }

  if (stage === "failed") {
    return (
      <span aria-hidden data-decorative="" className="absolute inset-0 grid place-items-center bg-surface-2">
        <span className="font-display text-lg text-line-strong">サイソク</span>
      </span>
    );
  }

  const coverSrc = cover && cover !== original ? cover : null;
  if (stage === "cover" && coverSrc) {
    return <Image {...props} src={coverSrc} data-thumb="" onError={() => setStage("failed")} />;
  }

  const webp = typeof original === "string" ? ytWebp(original) : original;
  const large = typeof original === "string" ? largeThumb(original) : null;
  const src =
    stage === "fallback" && typeof original === "string"
      ? large ?? original.replace("maxresdefault", "hqdefault")
      : webp;

  const onError = () => {
    const canFallback =
      stage === "original" &&
      typeof original === "string" &&
      (original.includes("maxresdefault") || webp !== original || large !== null || srcSet !== null);
    setStage(canFallback ? "fallback" : coverSrc ? "cover" : "failed");
  };

  // 幅の候補がある写真は srcset 付きの <img> で出す（next/image は unoptimized だと srcset を作らない）
  const srcSet =
    stage === "original" && typeof webp === "string" && props.fill && props.sizes
      ? ownSrcSet(webp) ?? ytSrcSet(webp)
      : null;
  if (srcSet && typeof src === "string") {
    return (
      <SrcSetImg
        {...props}
        src={src}
        srcSet={srcSet}
        onFail={onError}
      />
    );
  }

  return <Image {...props} src={src} data-thumb="" onError={onError} />;
}

/** srcset 付きの <img>。React が動き出す前に失敗していたら、描いた直後に onFail を呼ぶ */
function SrcSetImg({ src, srcSet, onFail, ...props }: Omit<ImageProps, "src"> & { src: string; srcSet: string; onFail: () => void }) {
  const ref = useCallback(
    (img: HTMLImageElement | null) => {
      if (img && img.complete && img.naturalWidth === 0) onFail();
    },
    // 最初に描いたときだけ確かめる（以後の失敗は onError で届く）
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const { priority, preload: preloadProp, sizes, fetchPriority, alt, className, style } = props;
  const eager = Boolean(priority || preloadProp);
  // LCP 候補は HTML の先頭で先読みさせる（next/image の priority と同じ扱いを srcset 付きで）
  if (eager) preload(src, { as: "image", imageSrcSet: srcSet, imageSizes: sizes, fetchPriority: "high" });
  return (
    <img
      alt={alt}
      src={src}
      srcSet={srcSet}
      sizes={sizes}
      decoding="async"
      loading={eager ? undefined : "lazy"}
      fetchPriority={eager ? "high" : fetchPriority}
      data-nimg="fill"
      className={className}
      style={{ position: "absolute", height: "100%", width: "100%", inset: 0, color: "transparent", ...style }}
      data-thumb=""
      ref={ref}
      onError={onFail}
    />
  );
}
