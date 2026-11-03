import type { ReactNode } from "react";
import { loadDefaultJapaneseParser } from "budoux";
import { splitHeadline } from "@/lib/headline";

/*
 * 見出しの組版。2つのことをする。
 *
 * 1. 要点に蛍光ペンを引く
 *    優先順: 「」で囲まれた製品名・機能名 → 単位付きの数字（15万行、3200万ドル など）。
 *    「◯本」「◯件」は統合本数などの件数で、別の場所に大きく表示しているので対象外。
 *
 * 2. 文節の切れ目でだけ改行させる
 *    日本語は単語の間に空白が無く、ブラウザは「機能」のような語の途中でも改行してしまう。
 *    Google の BudouX で文節に分け、切れ目に <wbr> を入れる（ビルド時に処理するので表示は軽い）。
 *    CSS の word-break: auto-phrase は Safari が未対応のため、iPhone でも効くこの方式にしている。
 */

const parser = loadDefaultJapaneseParser();

const QUOTED = /「([^」]{2,24})」/;
const FIGURE = /\d[\d,.]*(?:万|億|兆)?(?:行|ドル|円|%|％|倍|人|時間|パラメータ)/;
const SPACE = /(　|\s+)/;

/**
 * BudouX は「AFCアジアカップ2026メンバーと」のような英数字とカタカナの続きを1つの文節にすることがある。
 * 1行に入らないと overflow-wrap: anywhere で「202｜6」のように数字の途中で折れてしまう（2026-11-03 の画面の点検）。
 * 長い文節だけ、英数字とカタカナの境目にも切れ目を足す（「第2回」「2026年」のような漢字との境目は切らない）。
 */
const LATIN_KATAKANA = /(?<=[A-Za-z0-9])(?=[\u30a1-\u30fa])|(?<=[\u30a1-\u30fa\u30fc])(?=[A-Za-z0-9])/;
export function splitLongChunk(chunk: string): string[] {
  if (chunk.length <= 6) return [chunk];
  return chunk.split(LATIN_KATAKANA).filter(Boolean);
}

/*
 * 行頭に来てはいけない記号（閉じかっこ・句読点・中黒・長音）の前と、行末に来てはいけない開きかっこの後ろには
 * 切れ目を入れない（2026-11-03 オーナー指摘: 行が「」、」で終わる・かっこだけが行に残る）
 */
const NO_BREAK_BEFORE = /^[」』）)\]、。，．・：；！？ー〜…]/;
const NO_BREAK_AFTER = /[「『（(\[]$/;

/** 文節に分ける（禁則に合わない切れ目はつなぐ）。テスト用に export */
export function phraseChunks(part: string): string[] {
  const out: string[] = [];
  for (const chunk of parser.parse(part).flatMap(splitLongChunk)) {
    const prev = out[out.length - 1];
    if (prev !== undefined && (NO_BREAK_BEFORE.test(chunk) || NO_BREAK_AFTER.test(prev))) out[out.length - 1] = prev + chunk;
    else out.push(chunk);
  }
  return out;
}

function phrases(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  text.split(SPACE).forEach((part, i) => {
    if (!part) return;
    // 空白（全角スペース含む）はそれ自体が改行の切れ目になる
    if (SPACE.test(part) && part.trim() === "") {
      nodes.push(part);
      return;
    }
    phraseChunks(part).forEach((chunk, j) => {
      if (j > 0) nodes.push(<wbr key={`${keyPrefix}-${i}-${j}`} />);
      nodes.push(chunk);
    });
  });
  return nodes;
}

/** 補足（全角の空白のあと）は別の行に出す。コピーしたときに区切りが残るよう、空白は読み上げ・表示しない形で残す */
function subLine(sub: string | null, keyPrefix: string): ReactNode {
  if (!sub) return null;
  return (
    <>
      <span className="sr-only">　</span>
      <span className="headline-sub">{phrases(sub, keyPrefix)}</span>
    </>
  );
}

/** 文節の切れ目でだけ改行させる（蛍光ペンは引かない。製品まとめの見出し用） */
export function Phrases({ text, inline = false }: { text: string; inline?: boolean }): ReactNode {
  // inline: 補足を別の行にしない（行数を限った一覧の中など）
  if (inline) return <span className="headline">{phrases(text, "p")}</span>;
  const { main, sub } = splitHeadline(text);
  return (
    <span className="headline">
      {phrases(main, "p")}
      {subLine(sub, "ps")}
    </span>
  );
}

export function Highlight({ text: full }: { text: string }): ReactNode {
  const { main: text, sub } = splitHeadline(full);
  let before = text;
  let marked = "";
  let after = "";

  const quoted = QUOTED.exec(text);
  const figure = quoted ? null : FIGURE.exec(text);

  if (quoted) {
    const start = quoted.index + 1;
    before = text.slice(0, start);
    marked = quoted[1];
    after = text.slice(start + marked.length);
  } else if (figure) {
    before = text.slice(0, figure.index);
    marked = figure[0];
    after = text.slice(figure.index + marked.length);
  }

  return (
    <span className="headline">
      {phrases(before, "b")}
      {marked && <mark>{marked}</mark>}
      {after && phrases(after, "a")}
      {subLine(sub, "s")}
    </span>
  );
}
