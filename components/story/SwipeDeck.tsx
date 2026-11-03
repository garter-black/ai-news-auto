"use client";

import dynamic from "next/dynamic";
import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { liveBundles } from "@/lib/story-window";
import { coverPath } from "@/lib/cover-path";

// 開いたときのデッキ（components/story/StoryDeck.tsx）は丸の表示に要らないので、後から読み込む。
// 丸に指が触れたとき・ブラウザが空いたときに先読みして、開くのを待たせない
const loadDeck = () => import("./StoryDeck");
const Deck = dynamic(loadDeck, { ssr: false });

export type StoryItem = {
  slug: string;
  title: string;
  gist: string;
  highlights: string[];
  image: string;
  company: string;
  color: string;
  at: string;
  usable: "go" | "partial" | "wait" | null;
  usableLabel: string | null;
  japan: string | null;
  free: string | null;
  price: string | null;
};

/**
 * cover は丸の表紙（束どうしで重ならないようサーバー側で選ぶ）。items は新しい順、at で24時間を数える。
 * tier は並びの段（0=先頭固定、1=読者の関心の中心、2=その他）。同じ段の中は新しい順
 */
export type Bundle = { key: string; label: string; cover?: string; tier?: number; items: StoryItem[] };

/**
 * 丸の一覧に渡す軽い束。丸に要るのは件数・1本目の題名・表紙・24時間の判定だけなので、
 * 要約や要点（デッキで読む中身）はトップの HTML に載せず、開くときに /stories.json から読む（app/stories.json/route.ts）。
 * トップの HTML が約 80KB（gzip で約 20KB）軽くなり、最初の表示が早くなる
 */
export type RailItem = Pick<StoryItem, "slug" | "title" | "image" | "at">;
export type RailBundle = Omit<Bundle, "items"> & { items: RailItem[] };

/** デッキの中身（全部の束）。1回だけ読み、失敗したら次に開くときに読み直す */
let fullBundles: Promise<Map<string, StoryItem>> | null = null;
function loadFull(): Promise<Map<string, StoryItem>> {
  fullBundles ??= fetch("/stories.json")
    .then((res) => (res.ok ? (res.json() as Promise<Bundle[]>) : Promise.reject(new Error(String(res.status)))))
    .then((list) => new Map(list.flatMap((b) => b.items.map((it) => [it.slug, it] as const))))
    .catch((error) => {
      fullBundles = null;
      throw error;
    });
  return fullBundles;
}

/** 軽い束に中身を戻す。読めなかった記事は題名と写真だけで出す（デッキは開ける） */
function withDetails(bundle: RailBundle, details: Map<string, StoryItem> | null): Bundle {
  return {
    ...bundle,
    items: bundle.items.map(
      (it) =>
        details?.get(it.slug) ?? { ...it, gist: "", highlights: [], company: "", color: "#1d4a8f", usable: null, usableLabel: null, japan: null, free: null, price: null },
    ),
  };
}

/** デッキの JS と中身を先に読んでおく（丸に触れたとき・ブラウザが空いたとき） */
function prefetchDeck() {
  void loadDeck().catch(() => {});
  void loadFull().catch(() => {});
}

/** サイトの写真（/img/<名前>.jpg。lib/thumb-url.ts と同じ形） */
const OWN_IMAGE = /^((?:https:\/\/[^/]+)?\/img\/[A-Za-z0-9][A-Za-z0-9_-]*)\.jpg$/;

/** 丸の表紙（64〜76px）用の小さい画像。縮小版を出せる配信元だけ差し替える */
function ringImage(url: string): string {
  // サイトの写真は幅400pxの WebP（約10〜20KB。元の JPEG は1枚60〜140KBあり、丸15個で1MBを超えていた）。
  // 無い古い写真は worker が元の JPEG を返す
  const own = OWN_IMAGE.exec(url);
  if (own) return `${own[1]}.w400.webp`;
  if (url.startsWith("https://i.ytimg.com/vi/")) return url.replace(/(maxres|hq|sd)default/, "mqdefault");
  if (url.startsWith("https://prcdn.freetls.fastly.net/")) {
    return url.replace(/([?&])width=\d+/, "$1width=320").replace(/([?&])height=\d+/, "$1height=320");
  }
  return url;
}

/*
 * 閲覧時刻（分単位）。トップは静的ページで次のデプロイまでキャッシュされるので、
 * 24時間を過ぎた記事はブラウザ側で丸から外す。サーバー描画とハイドレーションでは null（=サーバーの束のまま）。
 */
const minuteNow = () => Math.floor(Date.now() / 60_000) * 60_000;
function subscribeClock(onChange: () => void) {
  const id = window.setInterval(onChange, 60_000);
  return () => window.clearInterval(id);
}

/**
 * トップ上段の「ストーリーの丸」と、タップで開く振り分けデッキ。
 * 右スワイプ＝気になる（取っておく）、左＝スキップ、ボタンで今すぐ読む。
 * 束を見終えたら「気になった記事」をまとめて出し、次の束へ進める。
 */
/** 丸の写真（表紙）と同じ記事。見つからなければ1本目 */
function previewOf(b: { cover?: string; items: { title: string; image?: string }[] }) {
  return (b.cover ? b.items.find((it) => it.image === b.cover) : undefined) ?? b.items[0];
}

export default function StoryRail({ bundles: built }: { bundles: RailBundle[] }) {
  const now = useSyncExternalStore(subscribeClock, minuteNow, () => null);
  const bundles = useMemo(() => (now === null ? built : liveBundles(built, now)), [built, now]);
  // 開いている束はキーで持つ（時刻が進んで丸の並びが変わっても、見ている束がずれない）
  const [openKey, setOpenKey] = useState<string | null>(null);
  const found = openKey === null ? -1 : bundles.findIndex((b) => b.key === openKey);
  const open = found < 0 ? null : found;
  const setOpen = (i: number | null) => setOpenKey(i === null ? null : bundles[i]?.key ?? null);
  const [seen, setSeen] = useState<Record<string, boolean>>({});
  // デッキの中身（null=まだ読んでいない、false=読めなかった）
  const [details, setDetails] = useState<Map<string, StoryItem> | null | false>(null);
  useEffect(() => {
    if (openKey === null || details) return;
    let alive = true;
    loadFull().then((map) => alive && setDetails(map), () => alive && setDetails(false));
    return () => { alive = false; };
  }, [openKey, details]);
  const rail = useRef<HTMLDivElement>(null);
  const railId = useId();
  const [scrollState, setScrollState] = useState({ overflow: false, previous: false, next: false });

  useEffect(() => {
    const el = rail.current;
    if (!el) return;
    const update = () => {
      const remaining = el.scrollWidth - el.clientWidth - el.scrollLeft;
      setScrollState({ overflow: el.scrollWidth > el.clientWidth + 1, previous: el.scrollLeft > 1, next: remaining > 1 });
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => { el.removeEventListener("scroll", update); observer.disconnect(); };
  }, [bundles.length]);

  const moveRail = (direction: -1 | 1) => {
    const el = rail.current;
    if (!el) return;
    el.scrollBy({ left: direction * Math.max(240, el.clientWidth * 0.7), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };

  // デッキの JS は、ブラウザが空いたときに先読みしておく（丸を押してから待たせない）
  useEffect(() => {
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
    const start = () => (w.requestIdleCallback ? w.requestIdleCallback(prefetchDeck, { timeout: 5000 }) : window.setTimeout(prefetchDeck, 3000));
    if (document.readyState === "complete") start();
    else window.addEventListener("load", start, { once: true });
    return () => window.removeEventListener("load", start);
  }, []);

  const isOpen = open !== null;
  useEffect(() => {
    if (!isOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // 記事への画面遷移でもアンマウント時に解除し、移動先のスクロールを妨げない。
    return () => { document.body.style.overflow = previousOverflow; };
  }, [isOpen]);

  // 表紙は lib/stories.ts で丸ごとに違う見た目を選んである
  const covers = bundles.map((b) => ringImage(b.cover || b.items[0]?.image || ""));

  if (bundles.length === 0) return null;

  return (
    <>
      <style>{CSS}</style>
      <div className="sr-wrap">
      {scrollState.overflow && <button type="button" className="sr-arrow sr-arrow-prev" aria-label="ストーリーを左に移動" aria-controls={railId} aria-disabled={!scrollState.previous} onClick={() => moveRail(-1)}><span aria-hidden>‹</span></button>}
      <div id={railId} ref={rail} className="sr" role="list" aria-label="ストーリー">
        {bundles.map((b, i) => (
          <button key={b.key} type="button" role="listitem" className={`sr-ring ${seen[b.key] ? "seen" : ""}`} aria-label={`${b.label}（${b.items.length}本）`} onPointerDown={prefetchDeck} onFocus={prefetchDeck} onClick={() => setOpen(i)}>
            <span className="sr-img">
              {/* LCP（下の大きな写真）と取り合わないよう低優先度。画面外の丸は近づいてから読む。
                  読めなければ1本目の記事の独自カバーに替える（app/layout.tsx の画像の受け皿） */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={covers[i]} alt="" width={76} height={76} decoding="async" fetchPriority="low" loading={i < 6 ? undefined : "lazy"} data-fallback={b.items[0] ? coverPath(b.items[0]) : undefined} />
              {/* 件数は丸の右下の小さな札（スマホは文字の行を減らして横に5個近く並べる） */}
              <span className="sr-n" data-allow-small="" aria-hidden>{b.items.length}</span>
            </span>
            <span className="sr-label" title={b.label}>{b.label}</span>
            {/* PC で添える題名は、丸の写真と同じ記事のもの（表紙は丸どうしで重ならないよう別の記事を選ぶので、1本目の題名だと写真と食い違っていた。2026-11-04 の点検） */}
            <span className="sr-preview" title={previewOf(b)?.title}>{Array.from(previewOf(b)?.title ?? "").slice(0, 26).join("")}{(previewOf(b)?.title.length ?? 0) > 26 ? "…" : ""}</span>
          </button>
        ))}
      </div>
      {scrollState.overflow && <button type="button" className="sr-arrow sr-arrow-next" aria-label="ストーリーを右に移動" aria-controls={railId} aria-disabled={!scrollState.next} onClick={() => moveRail(1)}><span aria-hidden>›</span></button>}
      </div>
      {open !== null && details === null && (
        // 中身を読み込むあいだ（先読みしていればほぼ一瞬）。閉じるボタンだけ出す
        <div className="dk" role="dialog" aria-modal="true" aria-label={bundles[open].label} aria-busy="true">
          <div className="dk-top">
            <div className="dk-bar">
              <button type="button" className="dk-close" onClick={() => setOpen(null)} aria-label="閉じる">
                <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
              </button>
              <span className="dk-title">{bundles[open].label}</span>
            </div>
          </div>
        </div>
      )}
      {open !== null && details !== null && (
        <Deck
          key={bundles[open].key}
          bundle={withDetails(bundles[open], details || null)}
          next={bundles[open + 1] && withDetails(bundles[open + 1], details || null)}
          onNext={() => {
            setSeen((s) => ({ ...s, [bundles[open].key]: true }));
            setOpen(open + 1 < bundles.length ? open + 1 : null);
          }}
          onClose={() => {
            setSeen((s) => ({ ...s, [bundles[open].key]: true }));
            setOpen(null);
          }}
        />
      )}
    </>
  );
}

const CSS = `
.sr-wrap{position:relative}
/* スマホは Instagram のストーリーのように小さな丸を詰めて並べる（375px で約4.6個見え、横にまだあると分かる） */
.sr{display:flex;gap:10px;overflow-x:auto;padding:12px 16px 8px;scrollbar-width:none;-webkit-overflow-scrolling:touch;scroll-padding-inline:16px}
.sr::-webkit-scrollbar{display:none}
/* 左右の矢印は PC だけ（スマホは指で横に送る） */
.sr-arrow{display:none;position:absolute;z-index:2;top:42px;place-items:center;width:38px;height:38px;border:1px solid var(--line-strong,#cbd5e1);border-radius:50%;background:var(--paper,#fff);color:var(--ink,#0b1a33);box-shadow:0 2px 10px rgba(11,26,51,.16);font-size:32px;line-height:1}
.sr-arrow span{display:block;transform:translateY(-2px)}
.sr-arrow-prev{left:2px}.sr-arrow-next{right:2px}
.sr-arrow[aria-disabled=true]{opacity:.38;cursor:default}
.sr-arrow:focus-visible{outline:2px solid #1d4a8f;outline-offset:2px}
/* ラベルは 13px（docs/typography.md）の1行。入りきらない分は「…」 */
.sr-ring{flex:none;display:flex;flex-direction:column;align-items:center;gap:5px;width:68px}
.sr-img{position:relative;width:64px;height:64px;border-radius:50%;padding:2.5px;background:conic-gradient(from 210deg,#2fd27f,#0a6fa0,#2fd27f);transition:transform .15s}
.sr-ring.seen .sr-img{background:var(--line-strong,#cfcfc7)}
.sr-img img{display:block;width:100%;height:100%;border-radius:50%;object-fit:cover;border:2.5px solid var(--paper,#fff);background:var(--surface-2,#eee)}
.sr-n{position:absolute;right:-3px;bottom:-1px;min-width:22px;height:20px;padding:0 5px;border-radius:999px;border:2px solid var(--paper,#fff);background:var(--ink,#0b1a33);color:var(--paper,#fff);font-family:var(--font-martian),monospace;font-size:12px;font-weight:600;line-height:16px;text-align:center}
.sr-label{width:100%;font-size:13px;font-weight:700;line-height:1.3;text-align:center;color:var(--ink,#111);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sr-preview{display:none}
.sr-ring:hover .sr-img{transform:scale(1.04)}
.sr-ring:focus-visible{outline:2px solid var(--ink,#111);outline-offset:4px;border-radius:12px}
@media (min-width:640px){.sr{gap:14px;padding:16px 32px 10px}.sr-ring{width:76px}}
/* PC は丸を少し大きくし、1本目の題名も添えて1列に均等に並べる。矢印はここから出す */
@media (min-width:1024px){
  .sr{gap:8px}
  .sr-arrow{display:grid}
  .sr-ring{flex:0 0 120px;min-width:120px;max-width:120px;gap:7px}
  .sr-img{width:76px;height:76px;padding:3px}
  .sr-label{font-size:14px}
  .sr-preview{width:100%;font-size:13px;line-height:1.4;text-align:center;color:var(--muted,#52524d);display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;overflow-wrap:anywhere}
}

.dk{position:fixed;inset:0;z-index:90;background:#0b0d10;color:#f4f5f7;font-family:var(--font-sans);-webkit-font-smoothing:antialiased;display:flex;flex-direction:column;overscroll-behavior:contain;touch-action:pan-y}
.dk-top{padding:10px 14px 6px}
.dk-progress{display:flex;gap:4px}
.dk-progress span{flex:1;height:2.5px;border-radius:2px;background:rgba(255,255,255,.22)}
.dk-progress span.done,.dk-progress span.now{background:#f4f5f7}
.dk-bar{display:flex;align-items:center;gap:8px;margin-top:9px}
.dk-close,.dk-gridbtn{width:34px;height:34px;display:grid;place-items:center;border-radius:50%;color:#f4f5f7}
.dk-gridbtn[aria-pressed=true]{background:#f4f5f7;color:#0b0d10}
.dk-title{font-weight:900;font-size:16px}
.dk-count{font-family:var(--font-martian),monospace;font-size:13px;color:#9aa1ab}
.dk-mode{margin-left:auto;display:flex;border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:2px}
.dk-mode button{font-size:13px;font-weight:700;padding:5px 9px;border-radius:999px;color:#b9bfc8}
.dk-mode button[aria-pressed=true]{background:#f4f5f7;color:#0b0d10}

.dk-stage{position:relative;flex:1;min-height:0;margin:4px 12px 0}
.dk-card{position:absolute;inset:0;border-radius:20px;overflow:hidden;background:#15181d;touch-action:none;user-select:none;cursor:grab;will-change:transform;backface-visibility:hidden;transition:transform .28s cubic-bezier(.2,.7,.2,1),opacity .28s}
.dk-card.is-top{box-shadow:0 18px 40px rgba(0,0,0,.45)}
.dk-card.is-peek{transform:scale(.95) translateY(10px);opacity:.55;pointer-events:none}
.dk-stamp{opacity:0}
.dk-stamp{position:absolute;top:22px;padding:6px 14px;border:3px solid;border-radius:10px;font-weight:900;font-size:22px;letter-spacing:.06em;pointer-events:none}
.dk-stamp.keep{left:18px;color:#2fd27f;transform:rotate(-12deg)}
.dk-stamp.skip{right:18px;color:#c9ced6;transform:rotate(12deg)}

.c{height:100%;display:grid;grid-template-rows:54% 1fr}
.c-photo{position:relative;overflow:hidden}
.c-photo img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;pointer-events:none}
.c-shade{position:absolute;inset:0;background:linear-gradient(180deg,rgba(21,24,29,0) 35%,rgba(21,24,29,.78) 74%,#15181d 100%)}
.c-head{position:absolute;left:16px;right:16px;bottom:6px}
.c-meta{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;letter-spacing:.04em;color:#e6e8eb}
.c-meta i{width:4px;height:14px;border-radius:1px}
.c-meta time{font-family:var(--font-martian),monospace;font-weight:400;font-size:13px;color:#aeb4bc;margin-left:4px}
.c-title{margin-top:7px;font-weight:900;font-size:clamp(19px,5.6vw,24px);line-height:1.42;text-wrap:balance;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden;text-shadow:0 1px 12px rgba(0,0,0,.4)}
.c-info{display:flex;flex-direction:column;gap:13px;padding:12px 16px 16px;min-height:0;overflow:hidden}
.c-verdict{display:flex;align-items:center;gap:12px}
.c-chip{font-weight:900;font-size:14px;padding:6px 13px;border-radius:999px;white-space:nowrap}
.c-chip.go{background:#2fd27f;color:#04170c}
.c-chip.partial{border:1.5px solid #9fb3c8;color:#9fb3c8}
.c-chip.wait{border:1.5px solid #8a919b;color:#8a919b}
.c-verdict dl{display:grid;gap:2px;font-size:13px;min-width:0}
.c-verdict dl div{display:flex;gap:8px;min-width:0}
.c-verdict dt{color:#9aa1ab;flex:none}
.c-verdict dd{color:#b9bfc8;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.c-gist{font-size:16px;line-height:1.8;color:#b9bfc8;display:-webkit-box;-webkit-line-clamp:5;-webkit-box-orient:vertical;overflow:hidden}
.c-points{display:grid;gap:8px;font-size:15px;line-height:1.6;color:#b9bfc8;counter-reset:p}
.c-points li{display:grid;grid-template-columns:18px 1fr;gap:6px;counter-increment:p}
.c-points li::before{content:counter(p);font-family:var(--font-martian),monospace;font-size:13px;color:#7d8590;padding-top:2px}

.dk-actions{display:grid;grid-template-columns:64px 1fr 64px;align-items:center;gap:14px;padding:14px 20px calc(14px + env(safe-area-inset-bottom))}
.dk-act{display:flex;flex-direction:column;align-items:center;gap:3px;font-size:12px;font-weight:700;color:#9aa1ab}
.dk-act svg{width:48px;height:48px;padding:13px;border-radius:50%;border:1.5px solid rgba(255,255,255,.2)}
.dk-act.keep{color:#2fd27f}
.dk-bg{display:none}
.dk-nav{position:absolute;top:28%;z-index:3;transform:translateY(-50%);display:grid;place-items:center;width:40px;height:40px;border-radius:50%;background:rgba(11,13,16,.6);color:#f4f5f7;border:1px solid rgba(255,255,255,.18)}
.dk-nav span{display:none}
.dk-nav.prev{left:6px}
.dk-nav.next{right:6px}
.dk-nav:focus-visible,.dk-act:focus-visible,.dk-read:focus-visible{outline:2px solid #2fd27f;outline-offset:3px}
@media (min-width:768px){
  .dk-bg{display:block;position:absolute;inset:-40px;width:calc(100% + 80px);height:calc(100% + 80px);object-fit:cover;filter:blur(40px) brightness(.35) saturate(1.2);z-index:0;pointer-events:none}
  .dk-top,.dk-actions,.dk-sum{position:relative;z-index:1;width:100%;max-width:520px;margin-inline:auto}
  .dk-stage{z-index:1;width:min(460px,100%);margin:6px auto 0;max-height:760px}
  .dk-nav{top:50%;width:64px;height:64px;background:rgba(255,255,255,.1);border-color:rgba(255,255,255,.25);gap:0;grid-template-rows:auto auto;padding-top:6px}
  .dk-nav:hover{background:#f4f5f7;color:#0b0d10}
  .dk-nav span{display:block;font-size:12px;font-weight:700;margin-top:-6px}
  .dk-nav.prev{left:-100px}
  .dk-nav.next{right:-100px}
  .dk-actions{max-width:460px}
}
.dk-act.keep svg{border-color:#2fd27f}
.dk-read{height:50px;display:flex;align-items:center;justify-content:center;border-radius:999px;background:#f4f5f7;color:#0b0d10;font-weight:700;font-size:15px}

.dk-sum{flex:1;display:flex;flex-direction:column;gap:14px;padding:18px 20px 28px;overflow-y:auto}
.dk-sum-k{font-weight:900;font-size:22px}
.dk-sum-list{display:grid;gap:10px}
.dk-sum-list a{display:grid;grid-template-columns:88px 1fr;gap:12px;align-items:center;font-size:15px;font-weight:700;line-height:1.5}
.dk-sum-list img{width:88px;aspect-ratio:16/9;object-fit:cover;border-radius:8px}
.dk-next{margin-top:auto;height:52px;border-radius:999px;background:#f4f5f7;color:#0b0d10;font-weight:900;font-size:15px}
.dk-back{font-size:14px;font-weight:700;color:#9aa1ab;padding:6px}

.dk-grid{position:absolute;inset:0;z-index:2;background:rgba(0,0,0,.55);display:flex;align-items:flex-end}
.dk-grid-in{width:100%;max-height:78%;overflow-y:auto;background:#15181d;border-radius:20px 20px 0 0;padding:18px 16px 24px}
.dk-grid-h{font-weight:900;font-size:15px;margin-bottom:12px}
.dk-grid ul{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.dk-grid a{display:flex;flex-direction:column;gap:6px;position:relative}
.dk-grid img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:8px}
.g-t{font-size:13px;font-weight:700;line-height:1.45;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.g-v{position:absolute;top:6px;left:6px;font-size:12px;font-weight:900;padding:2px 7px;border-radius:999px;background:rgba(0,0,0,.6);color:#c9ced6}
.g-v.go{background:#2fd27f;color:#04170c}
@media (prefers-reduced-motion:reduce){.dk *{transition:none!important}}
`;
