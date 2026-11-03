"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { smallThumb } from "@/lib/thumb-url";
import { normalizeForSearch, normalizeTags, queryTerms, rankHits, scoreRow, type SearchRow } from "@/lib/search-match";

type Props = {
  chunks: number;
  version: string;
  genres: Record<string, string>;
  genreOrder: string[];
  suggestions: string[];
};

type Prepared = { row: SearchRow; title: string; rest: string; tags: string[]; order: number };

const PAGE = 40;

/** URL の ?q= を読む（静的なページなので、サーバーではなくブラウザで読む） */
function readQuery(): string {
  try {
    return new URLSearchParams(window.location.search).get("q") ?? "";
  } catch {
    return "";
  }
}

function formatDate(ymd: string): string {
  const [y, m, d] = ymd.split("-");
  return y && m && d ? `${y}/${m}/${d}` : ymd;
}

/**
 * 検索の本体。索引を新しい方から順に読み、読めた分から結果を出す（1つ目は約40KB）。
 * 入力のたびに URL の ?q= を書き換える（戻る・共有で同じ結果に戻れる）。
 */
export default function SearchView({ chunks, version, genres, genreOrder, suggestions }: Props) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Prepared[]>([]);
  const [loaded, setLoaded] = useState(0);
  const [failed, setFailed] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const [genre, setGenre] = useState("");
  const input = useRef<HTMLInputElement>(null);

  // 最初に URL の言葉を入れる。言葉が無ければ入力欄にすぐ打てるようにする
  useEffect(() => {
    const q = readQuery();
    setQuery(q);
    if (!q) input.current?.focus();
  }, []);

  // 索引を順に読む（読めた分から検索に使う）
  useEffect(() => {
    let alive = true;
    (async () => {
      for (let n = 0; n < chunks; n++) {
        try {
          const res = await fetch(`/search-index/${n}.json?v=${version}`);
          // 索引のファイルの数は、検索に出さない記事を除いた数で決まる（build_index.mjs）。渡された数より少なければ、そこで読み終わり
          if (res.status === 404 && n > 0) {
            if (alive) setLoaded(chunks);
            return;
          }
          if (!res.ok) throw new Error(String(res.status));
          const rows = (await res.json()) as SearchRow[];
          if (!alive) return;
          setItems((prev) => [
            ...prev,
            ...rows.map((row, i) => ({
              row,
              title: normalizeForSearch(row[1]),
              rest: normalizeForSearch(`${row[2]} ${row[3]} ${genres[row[4]] ?? ""}`),
              tags: normalizeTags(row[3]),
              order: prev.length + i,
            })),
          ]);
          setLoaded(n + 1);
        } catch {
          if (alive) setFailed(true);
          return;
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [chunks, version, genres]);

  // URL を入力に合わせる（履歴は増やさない）
  const syncUrl = useCallback((q: string) => {
    try {
      const url = new URL(window.location.href);
      if (q) url.searchParams.set("q", q);
      else url.searchParams.delete("q");
      window.history.replaceState(window.history.state, "", url.toString());
    } catch {
      // 何もしない
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => syncUrl(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query, syncUrl]);

  useEffect(() => setLimit(PAGE), [query, genre]);

  const terms = useMemo(() => queryTerms(query), [query]);
  const results = useMemo(() => {
    if (terms.length === 0) return [];
    const hits: { item: Prepared; score: number }[] = [];
    for (const item of items) {
      if (genre && item.row[4] !== genre) continue;
      const score = scoreRow(terms, item.title, item.rest, item.tags);
      if (score > 0) hits.push({ item, score });
    }
    // 当たりの強さ（題名＞会社・製品のタグ＞要約）→ 新しい順（lib/search-match.ts の rankHits）
    return rankHits(hits);
  }, [items, terms, genre]);

  // 当たった記事のジャンル（絞り込みの切り替えに出す。タブと同じ並び）
  const hitGenres = useMemo(() => {
    if (terms.length === 0) return [];
    const count = new Map<string, number>();
    for (const item of items) {
      if (scoreRow(terms, item.title, item.rest, item.tags) > 0) count.set(item.row[4], (count.get(item.row[4]) ?? 0) + 1);
    }
    return [...count.entries()].sort(
      (a, b) => (genreOrder.indexOf(a[0]) + 1 || 99) - (genreOrder.indexOf(b[0]) + 1 || 99) || b[1] - a[1],
    );
  }, [items, terms, genreOrder]);

  const loading = loaded < chunks && !failed;
  const q = query.trim();

  return (
    <div className="mt-6">
      <form
        role="search"
        action="/search"
        onSubmit={(e) => {
          e.preventDefault();
          input.current?.blur();
          syncUrl(q);
        }}
      >
        <label className="flex h-12 items-center gap-2 rounded-md border border-line-strong bg-paper px-3 focus-within:border-accent">
          <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5 shrink-0 text-muted" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <circle cx="10.5" cy="10.5" r="6.5" />
            <path d="m15.5 15.5 5 5" />
          </svg>
          <span className="sr-only">検索する言葉</span>
          <input
            ref={input}
            type="search"
            name="q"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="会社名・サービス名・制度の名前など"
            enterKeyHint="search"
            autoComplete="off"
            className="min-w-0 flex-1 bg-transparent text-desc text-ink outline-none placeholder:text-muted"
          />
        </label>
      </form>

      {/* 結果は索引を読んでから出る（URL の ?q= も表示後に読む）ので、下の場所を先に取っておく。
          後からフッターが押し下げられるずれ（CLS）を防ぐ。2026-11-03 の画面の点検で 0.44 だった */}
      <div className="min-h-[70vh]">
      {!q && (
        <div className="mt-6">
          <p className="text-meta font-bold text-muted">よく探される言葉</p>
          <ul className="mt-2 flex flex-wrap gap-2">
            {suggestions.map((s) => (
              <li key={s}>
                <button
                  type="button"
                  onClick={() => setQuery(s)}
                  className="rounded-md border border-line px-3 py-1.5 text-meta font-bold text-ink-soft transition-colors hover:border-ink hover:text-ink"
                >
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {q && (
        <div>
          <p className="mt-5 text-note text-muted" aria-live="polite">
            {loading && results.length === 0 ? (
              <>「<span className="font-bold text-ink">{q}</span>」の記事を探しています…</>
            ) : (
              <>
                「<span className="font-bold text-ink">{q}</span>」の記事 <span className="index-num font-bold text-ink">{results.length}</span>本
                {loading && "（読み込み中…）"}
              </>
            )}
            {failed && "（索引の一部を読めませんでした）"}
          </p>

          {hitGenres.length > 1 && (
            <div className="-mx-4 mt-3 overflow-x-auto px-4 [scrollbar-width:none] sm:mx-0 sm:px-0 [&::-webkit-scrollbar]:hidden">
              <ul className="flex w-max gap-2 sm:w-auto sm:flex-wrap">
                {[["", 0] as [string, number], ...hitGenres].map(([slug, count]) => (
                  <li key={slug || "all"}>
                    <button
                      type="button"
                      aria-pressed={genre === slug}
                      onClick={() => setGenre(slug)}
                      className={`rounded-md border px-3 py-1.5 text-meta font-bold transition-colors ${
                        genre === slug ? "border-ink bg-ink text-paper" : "border-line text-ink-soft hover:border-ink hover:text-ink"
                      }`}
                    >
                      {slug ? genres[slug] ?? slug : "すべて"}
                      {slug && <span className="index-num ml-1.5 font-normal opacity-70">{count}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {results.length > 0 ? (
            <ul className="mt-5 grid gap-x-6 gap-y-5 sm:grid-cols-2">
              {results.slice(0, limit).map(({ row }) => (
                <li key={row[0]}>
                  <ResultCard row={row} genre={genres[row[4]] ?? ""} />
                </li>
              ))}
            </ul>
          ) : (
            !loading && (
              <p className="mt-8 text-desc text-ink-soft">
                見つかりませんでした。言葉を短くするか、別の言い方（例：「マイクロソフト」→「Microsoft」）で探してみてください。
              </p>
            )
          )}

          {results.length > limit && (
            <button
              type="button"
              onClick={() => setLimit((n) => n + PAGE)}
              className="mt-8 flex w-full items-center justify-center rounded-full border border-line-strong py-3 text-note font-bold text-ink transition-colors hover:border-ink"
            >
              もっと見る（残り{results.length - limit}本）
            </button>
          )}
        </div>
      )}
      </div>
    </div>
  );
}

/** 結果のカード（小さい写真＋見出し＋要約。写真の枠は先に取っておき、読み込みで高さが変わらない） */
function ResultCard({ row, genre }: { row: SearchRow; genre: string }) {
  const [slug, title, summary, , , date, thumb] = row;
  return (
    <Link href={`/articles/${slug}`} className="group grid grid-cols-[112px_1fr] gap-3 sm:grid-cols-[136px_1fr]">
      <span className="relative block aspect-video overflow-hidden rounded-md bg-surface-2">
        {thumb && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={smallThumb(thumb, "mqdefault")} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover" />
        )}
      </span>
      <span className="min-w-0">
        <span className="block text-meta text-muted">
          {genre && <span className="font-bold text-accent">{genre}</span>}
          <span className="index-num ml-2">{formatDate(date)}</span>
        </span>
        <span className="mt-1 line-clamp-3 block text-note font-bold leading-snug text-ink group-hover:text-accent sm:text-desc">{title}</span>
        <span className="mt-1 line-clamp-2 hidden text-note leading-relaxed text-ink-soft sm:block">{summary}</span>
      </span>
    </Link>
  );
}
