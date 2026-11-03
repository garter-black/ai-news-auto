"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { Bundle, StoryItem } from "./SwipeDeck";

/*
 * ストーリーの丸をタップしたときに開く振り分けデッキ（components/story/SwipeDeck.tsx から分けた）。
 * 丸の一覧（最初の表示）に要らない JS なので、初めて開くとき（か、ブラウザが空いたとき）に読み込む。
 */

type Mode = "easy" | "expert";

const SAVED_KEY = "saisoku:saved";

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

function ago(iso: string): string {
  const min = Math.max(1, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (min < 60) return `${min}分前`;
  const h = Math.round(min / 60);
  return h < 24 ? `${h}時間前` : `${Math.round(h / 24)}日前`;
}

export default function Deck({ bundle, next, onNext, onClose }: { bundle: Bundle; next?: Bundle; onNext: () => void; onClose: () => void }) {
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
                    <span className="g-t jp-wrap">{s.title}</span>
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
                <span className="jp-wrap">{s.title}</span>
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
          <h2 className="c-title jp-wrap">{it.title}</h2>
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
