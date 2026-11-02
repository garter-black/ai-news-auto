"use client";

import { useEffect } from "react";

/**
 * 第三者のスクリプト（AdSense など）を、ページの表示が終わったあとに読み込む。
 *
 * - ページの読み込み（load）が終わるまでは何もしない（本文・写真の表示と回線・CPU を取り合わない）
 * - 読み込み後、読者が操作したとき（スクロール・タップ・キー入力）か、ブラウザが空いたとき（最短 delayMs 後）の早いほうで1回だけ足す
 * - 同じ src を2回は足さない（ページ移動しても1回）
 */
export default function DeferredScript({
  src,
  id,
  crossOrigin,
  delayMs = 3500,
  dataset,
}: {
  src: string;
  id: string;
  crossOrigin?: "anonymous";
  delayMs?: number;
  dataset?: Record<string, string>;
}) {
  // data-* は文字列にして比べる（描画のたびに新しいオブジェクトが来ても読み込み直さない）
  const data = JSON.stringify(dataset ?? {});
  useEffect(() => {
    if (document.getElementById(id)) return;
    const events = ["scroll", "pointerdown", "keydown", "touchstart"] as const;
    let timer: number | undefined;
    let idle: number | undefined;
    let done = false;
    const inject = () => {
      if (done) return;
      done = true;
      cleanup();
      if (document.getElementById(id)) return;
      const script = document.createElement("script");
      script.id = id;
      script.src = src;
      script.async = true;
      if (crossOrigin) script.crossOrigin = crossOrigin;
      for (const [key, value] of Object.entries(JSON.parse(data) as Record<string, string>)) script.dataset[key] = value;
      document.body.appendChild(script);
    };
    const onInteract = () => inject();
    const cleanup = () => {
      for (const name of events) window.removeEventListener(name, onInteract);
      window.removeEventListener("load", afterLoad);
      if (timer) window.clearTimeout(timer);
      if (idle && "cancelIdleCallback" in window) window.cancelIdleCallback(idle);
    };
    function afterLoad() {
      for (const name of events) window.addEventListener(name, onInteract, { passive: true, once: true });
      timer = window.setTimeout(() => {
        if ("requestIdleCallback" in window) idle = window.requestIdleCallback(inject, { timeout: 2000 });
        else inject();
      }, delayMs);
    }
    if (document.readyState === "complete") afterLoad();
    else window.addEventListener("load", afterLoad, { once: true });
    return cleanup;
  }, [src, id, crossOrigin, delayMs, data]);
  return null;
}
