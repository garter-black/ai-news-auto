"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

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

/** cover は丸の表紙（束どうしで重ならないようサーバー側で選ぶ） */
export type Bundle = { key: string; label: string; cover?: string; items: StoryItem[] };
type Mode = "easy" | "expert";

const SAVED_KEY = "saisoku:saved";

/** 丸の表紙（64〜76px）用の小さい画像。縮小版を出せる配信元だけ差し替える */
function ringImage(url: string): string {
  if (url.startsWith("https://i.ytimg.com/vi/")) return url.replace(/(maxres|hq|sd)default/, "mqdefault");
  if (url.startsWith("https://prcdn.freetls.fastly.net/")) {
    return url.replace(/([?&])width=\d+/, "$1width=320").replace(/([?&])height=\d+/, "$1height=320");
  }
  return url;
}

function ago(iso: string): string {
  const min = Math.max(1, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (min < 60) return `${min}分前`;
  const h = Math.round(min / 60);
  return h < 24 ? `${h}時間前` : `${Math.round(h / 24)}日前`;
}

function readSaved(): string[] {
  try {
    return JSON.parse(localStorage.getItem(SAVED_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function writeSaved(slugs: string[]) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify(slugs.slice(-50)));
  } catch {}
}

/**
 * トップ上段の「ストーリーの丸」と、タップで開く振り分けデッキ。
 * 右スワイプ＝気になる（取っておく）、左＝スキップ、ボタンで今すぐ読む。
 * 束を見終えたら「気になった記事」をまとめて出し、次の束へ進める。
 */
export default function StoryRail({ bundles }: { bundles: Bundle[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const [seen, setSeen] = useState<Record<string, boolean>>({});
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
  }, [bundles]);

  const moveRail = (direction: -1 | 1) => {
    const el = rail.current;
    if (!el) return;
    el.scrollBy({ left: direction * Math.max(240, el.clientWidth * 0.7), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };

  useEffect(() => {
    if (open === null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // 記事への画面遷移でもアンマウント時に解除し、移動先のスクロールを妨げない。
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open]);

  // 表紙は lib/stories.ts で丸ごとに違う見た目を選んである
  const covers = bundles.map((b) => ringImage(b.cover || b.items[0]?.image || ""));

  return (
    <>
      <style>{CSS}</style>
      <div className="sr-wrap">
      {scrollState.overflow && <button type="button" className="sr-arrow sr-arrow-prev" aria-label="ストーリーを左に移動" aria-controls={railId} aria-disabled={!scrollState.previous} onClick={() => moveRail(-1)}><span aria-hidden>‹</span></button>}
      <div id={railId} ref={rail} className="sr" role="list" aria-label="ストーリー">
        {bundles.map((b, i) => (
          <button key={b.key} type="button" role="listitem" className={`sr-ring ${seen[b.key] ? "seen" : ""}`} onClick={() => setOpen(i)}>
            <span className="sr-img">
              {/* LCP（下の大きな写真）と取り合わないよう低優先度。画面外の丸は近づいてから読む */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={covers[i]} alt="" width={76} height={76} decoding="async" fetchPriority="low" loading={i < 5 ? undefined : "lazy"} />
            </span>
            <span className="sr-label">{b.label}</span>
            <span className="sr-n">{b.items.length}本</span>
            <span className="sr-preview" title={b.items[0]?.title}>{Array.from(b.items[0]?.title ?? "").slice(0, 26).join("")}{(b.items[0]?.title.length ?? 0) > 26 ? "…" : ""}</span>
          </button>
        ))}
      </div>
      {scrollState.overflow && <button type="button" className="sr-arrow sr-arrow-next" aria-label="ストーリーを右に移動" aria-controls={railId} aria-disabled={!scrollState.next} onClick={() => moveRail(1)}><span aria-hidden>›</span></button>}
      </div>
      {open !== null && (
        <Deck
          key={bundles[open].key}
          bundle={bundles[open]}
          next={bundles[open + 1]}
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

function Deck({ bundle, next, onNext, onClose }: { bundle: Bundle; next?: Bundle; onNext: () => void; onClose: () => void }) {
  const { items } = bundle;
  const [index, setIndex] = useState(0);
  const [kept, setKept] = useState<StoryItem[]>([]);
  const [mode, setMode] = useState<Mode>("easy");
  const [grid, setGrid] = useState(false);
  const busy = useRef(false);
  const start = useRef<{ x: number; id: number } | null>(null);
  const dx = useRef(0);
  const raf = useRef(0);
  const top = useRef<HTMLDivElement>(null);
  const done = index >= items.length;

  // 指に合わせた動きは React の再描画を通さず、カードの style を直接書き換える（カクつき防止）
  const paint = (x: number, animate: boolean) => {
    const el = top.current;
    if (!el) return;
    el.style.transition = animate ? "transform .28s cubic-bezier(.2,.7,.2,1)" : "none";
    el.style.transform = `translate3d(${x}px,0,0) rotate(${x / 24}deg)`;
    const keep = el.querySelector<HTMLElement>(".dk-stamp.keep");
    const skip = el.querySelector<HTMLElement>(".dk-stamp.skip");
    if (keep) keep.style.opacity = String(Math.max(0, Math.min(1, x / 90)));
    if (skip) skip.style.opacity = String(Math.max(0, Math.min(1, -x / 90)));
  };

  const decide = (dir: 1 | -1) => {
    if (done || busy.current) return;
    busy.current = true;
    const it = items[index];
    if (dir === 1) {
      setKept((k) => [...k, it]);
      writeSaved([...readSaved().filter((s) => s !== it.slug), it.slug]);
    }
    paint(dir * (window.innerWidth + 200), true);
    window.setTimeout(() => {
      busy.current = false;
      dx.current = 0;
      setIndex((i) => i + 1);
    }, 240);
  };

  const back = () => {
    if (index === 0 || busy.current) return;
    const prev = items[index - 1];
    setKept((k) => k.filter((s) => s.slug !== prev.slug));
    setIndex(index - 1);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowRight") decide(-1);
      if (e.key === "ArrowLeft") back();
      if (e.key === "ArrowUp" || e.key === "s") decide(1);
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (busy.current || (e.target as HTMLElement).closest("button,a")) return;
    start.current = { x: e.clientX, id: e.pointerId };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onMove = (e: React.PointerEvent) => {
    if (!start.current) return;
    dx.current = e.clientX - start.current.x;
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(() => paint(dx.current, false));
  };
  const onUp = () => {
    if (!start.current) return;
    start.current = null;
    cancelAnimationFrame(raf.current);
    if (dx.current > 90) decide(1);
    else if (dx.current < -90) decide(-1);
    else paint(0, true);
  };

  const it = items[index];
  // 次のカードは同じ要素のまま前に出す（画像の読み直しで一瞬消えないように）
  const stack = items.slice(index, index + 2).reverse();

  return (
    <div className="dk" role="dialog" aria-modal="true" aria-label={bundle.label}>
      <div className="dk-top">
        <div className="dk-progress" aria-hidden>
          {items.map((s, i) => (
            <span key={s.slug} className={i < index ? "done" : i === index ? "now" : ""} />
          ))}
        </div>
        <div className="dk-bar">
          <button type="button" className="dk-close" onClick={onClose} aria-label="閉じる">
            <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
          </button>
          <span className="dk-title">{bundle.label}</span>
          <span className="dk-count">{Math.min(index + 1, items.length)}/{items.length}</span>
          <div className="dk-mode" role="group" aria-label="読み方">
            <button type="button" aria-pressed={mode === "easy"} onClick={() => setMode("easy")}>かんたん</button>
            <button type="button" aria-pressed={mode === "expert"} onClick={() => setMode("expert")}>専門家</button>
          </div>
          <button type="button" className="dk-gridbtn" aria-pressed={grid} onClick={() => setGrid((g) => !g)} aria-label="一覧で選ぶ">
            <svg viewBox="0 0 20 20" width="17" height="17" aria-hidden><path d="M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3zM11 11h6v6h-6z" fill="none" stroke="currentColor" strokeWidth="1.7" /></svg>
          </button>
        </div>
      </div>

      {!done && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="dk-bg" src={it.image.replace("maxresdefault", "hqdefault")} alt="" aria-hidden />
      )}
      {!done && (
        <div className="dk-stage">
          {index > 0 && (
            <button type="button" className="dk-nav prev" onClick={back} aria-label="前の記事へ">
              <svg viewBox="0 0 20 20" width="22" height="22" aria-hidden><path d="M12.5 4.5L7 10l5.5 5.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              <span>前へ</span>
            </button>
          )}
          <button type="button" className="dk-nav next" onClick={() => decide(-1)} aria-label="次の記事へ">
            <svg viewBox="0 0 20 20" width="22" height="22" aria-hidden><path d="M7.5 4.5L13 10l-5.5 5.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            <span>次へ</span>
          </button>
          {stack.map((card) => {
            const isTop = card.slug === it.slug;
            return (
              <div
                key={card.slug}
                ref={isTop ? top : undefined}
                className={`dk-card ${isTop ? "is-top" : "is-peek"}`}
                aria-hidden={!isTop}
                onPointerDown={isTop ? onDown : undefined}
                onPointerMove={isTop ? onMove : undefined}
                onPointerUp={isTop ? onUp : undefined}
                onPointerCancel={isTop ? onUp : undefined}
              >
                <Card it={card} mode={mode} />
                <span className="dk-stamp keep">気になる</span>
                <span className="dk-stamp skip">興味なし</span>
              </div>
            );
          })}
        </div>
      )}

      {!done && (
        <div className="dk-actions">
          <button type="button" className="dk-act skip" onClick={() => decide(-1)}>
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg>
            <span>興味なし</span>
          </button>
          <Link href={`/articles/${it.slug}`} className="dk-read">今すぐ読む</Link>
          <button type="button" className="dk-act keep" onClick={() => decide(1)}>
            <svg viewBox="0 0 20 20" width="20" height="20" aria-hidden><path d="M6 3h8v14l-4-3-4 3z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" /></svg>
            <span>気になる</span>
          </button>
        </div>
      )}

      {done && <Summary kept={kept} next={next} onNext={onNext} onClose={onClose} />}

      {grid && (
        <div className="dk-grid" onClick={() => setGrid(false)}>
          <div className="dk-grid-in" onClick={(e) => e.stopPropagation()}>
            <p className="dk-grid-h">{bundle.label}から選ぶ（{items.length}本）</p>
            <ul>
              {items.map((s) => (
                <li key={s.slug}>
                  <Link href={`/articles/${s.slug}`}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={s.image.replace("maxresdefault", "hqdefault")} alt="" />
                    <span className="g-t">{s.title}</span>
                    {s.usableLabel && <span className={`g-v ${s.usable}`}>{s.usableLabel}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

function Summary({ kept, next, onNext, onClose }: { kept: StoryItem[]; next?: Bundle; onNext: () => void; onClose: () => void }) {
  return (
    <div className="dk-sum">
      <p className="dk-sum-k">{kept.length ? `気になった${kept.length}本` : "今回はピンとくるものがなかったようです"}</p>
      {kept.length > 0 && (
        <ul className="dk-sum-list">
          {kept.map((s) => (
            <li key={s.slug}>
              <Link href={`/articles/${s.slug}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={s.image.replace("maxresdefault", "hqdefault")} alt="" />
                <span>{s.title}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {next ? (
        <button type="button" className="dk-next" onClick={onNext}>
          次は「{next.label}」<span aria-hidden> →</span>
        </button>
      ) : (
        <button type="button" className="dk-next" onClick={onClose}>一覧に戻る</button>
      )}
      <button type="button" className="dk-back" onClick={onClose}>記事一覧から選ぶ</button>
    </div>
  );
}

function Card({ it, mode }: { it: StoryItem; mode: Mode }) {
  return (
    <div className="c">
      <div className="c-photo">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={it.image} alt="" draggable={false} onError={(e) => (e.currentTarget.src = it.image.replace("maxresdefault", "hqdefault"))} />
        <div className="c-shade" />
        <div className="c-head">
          <p className="c-meta">
            <i style={{ background: it.color }} />
            {it.company}
            <time suppressHydrationWarning>{ago(it.at)}</time>
          </p>
          <h2 className="c-title">{it.title}</h2>
        </div>
      </div>
      <div className="c-info">
        {it.usable && (
          <div className="c-verdict">
            <span className={`c-chip ${it.usable}`}>{it.usableLabel}</span>
            <dl>
              <div><dt>日本</dt><dd>{it.japan}</dd></div>
              <div><dt>料金</dt><dd>{it.free === "無料で試せる" ? "無料で試せる" : it.price}</dd></div>
            </dl>
          </div>
        )}
        {mode === "easy" ? (
          <p className="c-gist">{it.gist}</p>
        ) : (
          <ul className="c-points">{it.highlights.map((h) => <li key={h}>{h}</li>)}</ul>
        )}
      </div>
    </div>
  );
}

const CSS = `
.sr-wrap{position:relative}
.sr{display:flex;gap:12px;overflow-x:auto;padding:14px 20px 10px;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.sr::-webkit-scrollbar{display:none}
.sr-arrow{position:absolute;z-index:2;top:36px;display:grid;place-items:center;width:38px;height:38px;border:1px solid var(--line-strong,#cbd5e1);border-radius:50%;background:var(--paper,#fff);color:var(--ink,#0b1a33);box-shadow:0 2px 10px rgba(11,26,51,.16);font-size:32px;line-height:1}
.sr-arrow span{display:block;transform:translateY(-2px)}
.sr-arrow-prev{left:2px}.sr-arrow-next{right:2px}
.sr-arrow[aria-disabled=true]{opacity:.38;cursor:default}
.sr-arrow:focus-visible{outline:2px solid #1d4a8f;outline-offset:2px}
/* ラベルは 13px（docs/typography.md）。6 字の「今すぐ使える」が切れない幅にする */
.sr-ring{flex:none;display:flex;flex-direction:column;align-items:center;gap:5px;width:108px}
.sr-img{width:64px;height:64px;border-radius:50%;padding:2.5px;background:conic-gradient(from 210deg,#2fd27f,#0a6fa0,#2fd27f);transition:transform .15s}
.sr-ring.seen .sr-img{background:var(--line-strong,#cfcfc7)}
.sr-img img{width:100%;height:100%;border-radius:50%;object-fit:cover;border:2.5px solid var(--paper,#fff);background:var(--surface-2,#eee)}
.sr-label{width:100%;font-size:13px;font-weight:700;line-height:1.3;text-align:center;color:var(--ink,#111);overflow-wrap:anywhere}
.sr-n{font-family:var(--font-martian),monospace;font-size:13px;color:var(--muted,#52524d);margin-top:-4px}
.sr-preview{width:100%;font-size:13px;line-height:1.4;text-align:center;color:var(--muted,#52524d);display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;overflow-wrap:anywhere}
.sr-ring:hover .sr-img{transform:scale(1.04)}
.sr-ring:focus-visible{outline:2px solid var(--ink,#111);outline-offset:4px;border-radius:12px}
@media (min-width:640px){.sr{padding:18px 32px 12px}}
/* PC は丸を少し大きくし、1列（最大12個）に均等に並べて横幅を使い切る */
@media (min-width:1024px){
  .sr{gap:8px}
  .sr-arrow{top:42px}
  .sr-ring{flex:0 0 120px;min-width:120px;max-width:120px;gap:7px}
  .sr-img{width:76px;height:76px;padding:3px}
  .sr-label{font-size:14px}
  .sr-n{font-size:13px}
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
