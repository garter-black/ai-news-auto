"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { INFLOW_BOT_UA, INTERNAL_PARAM, INTERNAL_STORAGE_KEY, classifyInflow, pageKey } from "@/lib/inflow";

/** そのタブで最初に開いたページ（外から来た入口）だけ数える。サイト内の移動では送らない */
let handled = false;

/**
 * 外から記事に来た人を1回だけ数える（cloudflare/inflow.ts の POST /api/aff/view）。
 * JS が動く実ブラウザだけが送るので、リンクプレビュー・curl・クローラーは数えない。
 * 自動ブラウザ（navigator.webdriver）・HeadlessChrome・Lighthouse・PageSpeed も送らない。
 * ?_internal=1 で開いたブラウザは、以後このブラウザでは数えない（?_internal=0 で戻る）。
 * Cookie・識別子・参照元の原文・クエリ全体は送らない（流入元の分類と utm の短い値だけ）。
 */
export default function InflowBeacon() {
  const pathname = usePathname();
  useEffect(() => {
    if (handled || !pathname) return;
    handled = true;
    try {
      const internal = new URLSearchParams(location.search).get(INTERNAL_PARAM);
      try {
        if (internal === "1") localStorage.setItem(INTERNAL_STORAGE_KEY, "1");
        if (internal === "0") localStorage.removeItem(INTERNAL_STORAGE_KEY);
        if (localStorage.getItem(INTERNAL_STORAGE_KEY) === "1") return;
      } catch { /* storage が使えなくても ?_internal=1 は下の classifyInflow で落ちる */ }
      if (navigator.webdriver || INFLOW_BOT_UA.test(navigator.userAgent) || typeof navigator.sendBeacon !== "function") return;
      const page = pageKey(pathname);
      const inflow = page ? classifyInflow(location.search, document.referrer, location.origin) : null;
      if (!page || !inflow) return;
      const payload = JSON.stringify({ p: page, s: inflow.source, c: inflow.campaign, t: inflow.content });
      const send = () => {
        if (document.visibilityState !== "visible") return false;
        try { navigator.sendBeacon("/api/aff/view", payload); } catch { /* 計測は表示を止めない */ }
        return true;
      };
      // 先読み（prerender）・裏タブで開いただけのものは、画面に出たときに送る
      if (send()) return;
      const onVisible = () => { if (send()) document.removeEventListener("visibilitychange", onVisible); };
      document.addEventListener("visibilitychange", onVisible);
    } catch { /* 計測は表示を止めない */ }
  }, [pathname]);
  return null;
}
