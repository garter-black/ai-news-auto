"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "saisoku:read-mode";
type Mode = "easy" | "expert";

/**
 * 「かんたんに読む／専門家として読む」の切り替え。サイソクの売りの1つ。
 * 本文は両方ともサーバーで描画済みで、ここでは #article-flow の data-read-mode を切り替えるだけ。
 * 選んだ読み方は覚えておき、次の記事でも同じ読み方で開く。
 */
export default function ReadingMode({ easyMinutes, expertMinutes }: { easyMinutes: number; expertMinutes: number }) {
  const [mode, setMode] = useState<Mode>("expert");

  useEffect(() => {
    let saved: Mode = "expert";
    try {
      if (localStorage.getItem(STORAGE_KEY) === "easy") saved = "easy";
    } catch {}
    apply(saved);
    setMode(saved);
  }, []);

  const choose = (next: Mode) => {
    setMode(next);
    apply(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {}
  };

  const options: { key: Mode; label: string; sub: string }[] = [
    { key: "easy", label: "要約版で読む", sub: `約${easyMinutes}分・用語を控えめに` },
    { key: "expert", label: "詳細版で読む", sub: `約${expertMinutes}分・数字や仕様まで` },
  ];

  return (
    <div className="mt-6 rounded-2xl border border-line-strong p-1.5" role="radiogroup" aria-label="記事の読み方">
      <div className="grid grid-cols-2 gap-1.5">
        {options.map((option) => {
          const active = mode === option.key;
          return (
            <button
              key={option.key}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => choose(option.key)}
              className={`rounded-xl px-3 py-2.5 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink ${
                active ? "bg-ink text-paper" : "text-ink hover:bg-surface"
              }`}
            >
              <span className="block text-note font-bold">{option.label}</span>
              <span className={`block text-meta ${active ? "opacity-75" : "text-muted"}`}>{option.sub}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function apply(mode: Mode) {
  document.getElementById("article-flow")?.setAttribute("data-read-mode", mode);
}
