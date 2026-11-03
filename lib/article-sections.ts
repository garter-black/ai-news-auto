/**
 * 記事本文の章（## 見出しの区切り）を、表示の直前に整える（2026-11-03 オーナー指摘）。
 *
 *  1. 中身なしの章を出さない（「元記事には比較は書かれていません。…判断できません。」だけの「ライバルと比べてどう？」など）
 *  2. 事実を含まない「つなぎ」の文（「〜にとっても重要です」「〜といえます」「判断材料になります」）と、中の人の注記を消す
 *  3. 決まった型の見出し（「自分に関係ある？」「もう使える？取引できる？」）を、記事ごとの見出し（記事 JSON の sectionTitles）か、
 *     ニュースサイトの普通の見出しに言い換える
 *
 * 1・2 の規則は scripts/section_gate.py（新しい記事を書き出す前の門）と同じ。変えるときは両方を直す
 * （tests/fixtures/section_gate_cases.json で同じ結果になることを tests/test_article_sections.cjs と tests/test_section_gate.py が確かめる）。
 * 本文 JSON の見出しは変えない（料金の表・図・計算機の差し込み位置や、検索に出す条件が決まった章名を使っているため）。
 */

// 中身が少なくても消さない章（記事の骨組み）
const PROTECTED_RE =
  /^(概要|ひとことで言うと|ひとすることで言うと|まとめ|これまでの流れ|続報|何が発表された|いつ、何が発表された|時系列で見る|この動画で語られていること)/;
// scripts/rivals.py が公式ページの数字で作る比較表（品質ゲートで確かめ済み）。触らない
export const DATA_RIVAL_HEADING = "同じクラスの製品と比べると？";
const RIVAL_RE = /^(ライバルと比べて|競合サービスとの比較|競合との比較|他の選択肢との比較|ほかの選択肢と比べて)|と比べて(どう|みると)/;
const NUMBERS_RE = /^(数字で見る|性能・ベンチマーク|この数字はどう読む|動画で示された数)/;
const PRICE_HEADING_RE = /^(料金|価格|いくら)/;
const PRICE_WORD_RE = /[0-9０-９]|無料|有料|月額|年額|円|ドル|ユーロ|価格|料金|課金|プラン|据え置き|値上げ|値下げ/;
const OUTDATED_MODEL_RE =
  /GPT-4o|GPT-4(?![.\d])|GPT‑4o|Claude 3(?:\.5)?(?![.\d])|Claude3|Gemini 1\.5|Gemini 1\.0|Llama 3(?![.\d])|\bo1(?:-preview|-mini)?\b/;

const META_RE = new RegExp(
  [
    "ここは編集部注です|以下は編集部の(?:知識|見立て)による補足です",
    "(?:元記事|元の記事|発表)(?:には|では)、?[^。]{0,30}(?:比較|ライバル|競合)[^。]{0,30}(?:書かれて|ありません|触れられて|示されて)",
    "優劣は[^。]{0,30}判断できません",
    "(?:前の版|前のモデル|従来)と比べられる数(?:値|字)は[^。]{0,30}(?:ありません|ない)",
    "比較表は作りません",
    "読者が次に取るべき(?:行動|アクション)(?:があります|は次のとおりです|です)",
    "今回の発表の位置づけ(?:は|として)",
  ].join("|"),
);

const NONANSWER_RE = new RegExp(
  [
    "書かれて(?:い)?ません|記載(?:は|が)?(?:ありません|ない|なし)|言及(?:は|が)?(?:ありません|なし|されていません)",
    "触れられて(?:い)?ません|示されて(?:い)?ません|明らかにされて(?:い)?ません|判断できません|判断はできません",
    "分かりません|わかりません|不明です|確認できていません|確認できません|公表されていません",
    "案内(?:は|が)?(?:ありません|されていません)|提示(?:は|が)?(?:ありません|なし|されていません)|発表されていません",
  ].join("|"),
);

const FILLER_RE = new RegExp(
  [
    "にとって(?:も)?(?:重要|大切|見逃せない|朗報|注目|うれしい)|と(?:い|言)え(?:ます|るでしょう)",
    "判断材料(?:に|と)?(?:なります|なる|です)|チェックしておきたい",
    "注目(?:の内容です|です|されそうです|が集まりそうです|が集まります|したいところです)|見逃せない",
    "影響(?:は|が)(?:薄い|小さい|限定的)(?:です|でしょう|といえます)|直接(?:的な|の)影響は(?:大きく|小さ)",
    "期待(?:されます|が高まります|できそうです)|参考になります|強いて言えば|関係する情報です",
    "に(?:も)?関係があります|役立つでしょう|気になるところです|目が離せません",
    "(?:機会|選択肢)が(?:生まれる|広がる)可能性があります|この立場への直接の影響",
  ].join("|"),
);

const FACT_RE = /[0-9０-９]|[A-Za-zＡ-Ｚａ-ｚ]{2,}|[「『]/;
// 読者への答えになっていない「元記事には〜書かれていません」「判断できません」の文（オーナーのルール A2）。文ごと消す。
// 「〇〇はまだ発表されていません」（何が未定かを主語にした答え）は消さない
const SOURCE_NONANSWER_RE = new RegExp(
  [
    "元記事(?:に|で)(?:は|も)?、?[^。\\n]{0,30}?(?:書かれて(?:い)?(?:ません|ない)|触れられて(?:い)?(?:ません|ない)|",
    "(?:記載|言及|明記)(?:は|が)?(?:ありません|ない|なし|されていません|されてい?ない))|",
    "[はも]、?(?:公式(?:ページ)?に)?記載(?:は|が)?(?:なし|ありません)|判断できません|判断できない状態です",
  ].join(""),
);
// 文の中のかっこ書きの注記（「（元記事では価格情報は言及なし）」）
const NONANSWER_PAREN_RE = /[（(](?=[^（）()]*元記事)(?=[^（）()]*(?:言及|記載|明記|書かれて|触れられ))[^（）()]{0,60}[）)]/g;
// 表の注記の「『公式に記載なし』は〜」→「空欄は〜」（表の「記載なし」のセルは空欄にする）
const EMPTY_TOKEN_RE = /「(?:公式に|料金ページに|元記事に)?記載なし」/g;

// 読者像の3つの立場の小見出し（「### 会社員として」「### 副業・独立を考えている人」「### お金・資産づくりの面で」。太字の行・「会社員として仕事で」も）。
// 小見出しの行は出さず、その下は事実があって答えになっている段落だけ残す（2026-11-03 オーナー決定・台帳 A24）。scripts/section_gate.py と同じ
export const PERSONA_SUBHEAD_RE =
  /^(?:#{3,4}\s*|\*\*)(?:会社員として(?:仕事で)?|副業・独立を考えている人|お金・資産づくりの面で)[^\n。]{0,12}?(?:\*\*)?\s*$/;
const PERSONA_FILLER_RE = new RegExp(
  [
    "この立場|立場への|直接(?:的な|の)?(?:影響|関係|優遇|メリット)[^。]{0,16}(?:小さ|な[いく]|ありません|薄|限定的)|強いて言えば|",
    "優遇措置(?:など)?はありません|収入にどうつながるか|直結(?:する(?:話|もの))?ではありません|直接(?:は)?関係(?:は|し)?(?:ない|ません)",
  ].join(""),
);
// 見出しの言葉だけ変えた「中身なしの章」の見出し（「比べられる数字は示されていない」「日本向けの提供条件はまだ分からない」。台帳 A24）。
// 「まだ分からないこと」（未定事項をまとめる決まった章）は対象外。scripts/section_gate.py の NONANSWER_TITLE_RE と同じ
export const NONANSWER_TITLE_RE = /(?:ない|なし|無し|未発表|未定|未公表|非公表|不明|分からない|わからない|未確認|言及なし|記載なし)[？?。]?$/;
export function isNonanswerTitle(title: string | null | undefined): boolean {
  const t = (title ?? "").trim();
  return Boolean(t) && !/^まだ分からないこと/.test(t) && NONANSWER_TITLE_RE.test(t);
}

// 呼びかけの「〜しましょう」「〜してみましょう」を「〜するとよいでしょう」にする（オーナーのルール A4）。scripts/section_gate.py の professional と同じ
const ICHIDAN_I = ["落ち", "用い", "起き", "生き", "でき", "過ぎ", "浴び", "伸び", "延び", "試み", "借り", "見", "居", "着", "似"];
const GODAN_SHI_COMPOUND = ["見直", "見通", "手放", "見逃", "見落", "見渡", "問い直", "引き出", "取り消", "打ち出", "乗り出", "洗い出", "割り出", "探し出", "見出"];
const GODAN_ROW: Record<string, string> = { い: "う", き: "く", ぎ: "ぐ", し: "す", ち: "つ", に: "ぬ", び: "ぶ", み: "む", り: "る" };
const E_ROW = new Set([..."えけげせぜてでねべぺめれへ"]);
const SURU_HEAD_RE = /(?:[㐀-鿿々]{2}|[ァ-ヴー]|[A-Za-zＡ-Ｚａ-ｚ0-9０-９]|[をのに])$/;
const TE_MASHOU_RE = /([てで])(みましょう|おきましょう|いきましょう)([。！]?)$/;
const INTRO_RE = /(見|整理し|確認し|比較し|紹介し)ていきましょう([。！]?)$/;
const MASHOU_RE = /ましょう([。！]?)$/;

function dictionaryForm(stem: string): string | null {
  if (!stem) return null;
  if (stem.endsWith("し")) {
    const head = stem.slice(0, -1);
    if (GODAN_SHI_COMPOUND.some((x) => head.endsWith(x))) return `${head}す`;
    return head + (!head || SURU_HEAD_RE.test(head) ? "する" : "す");
  }
  if (stem.endsWith("じ")) return `${stem}る`;
  const last = stem.slice(-1);
  if (ICHIDAN_I.some((x) => stem.endsWith(x)) || E_ROW.has(last)) return `${stem}る`;
  if (GODAN_ROW[last]) return stem.slice(0, -1) + GODAN_ROW[last];
  return null;
}

function openBrackets(text: string): number {
  let depth = 0;
  for (const ch of text) {
    if ("「『（(".includes(ch)) depth += 1;
    else if ("」』）)".includes(ch) && depth > 0) depth -= 1;
  }
  return depth;
}

/** 1文の文末の「〜しましょう」を「〜するとよいでしょう」に（「見ていきましょう」は「見ていきます」）。かっこの中や、言い換え方が分からない文はそのまま */
export function professionalSentence(sentence: string): string {
  const s = sentence.trimEnd();
  const tail = sentence.slice(s.length);
  if (!s.includes("ましょう") || openBrackets(s)) return sentence;
  const end = (mark: string | undefined) => (mark ? "。" : "");
  let m = INTRO_RE.exec(s);
  if (m) return `${s.slice(0, m.index)}${m[1]}ていきます${end(m[2])}${tail}`;
  m = TE_MASHOU_RE.exec(s);
  if (m) {
    const aux = ({ みましょう: "みる", おきましょう: "おく", いきましょう: "いく" } as Record<string, string>)[m[2]];
    return `${s.slice(0, m.index)}${m[1]}${aux}とよいでしょう${end(m[3])}${tail}`;
  }
  m = MASHOU_RE.exec(s);
  if (m) {
    const base = dictionaryForm(s.slice(0, m.index));
    if (base) return `${base}とよいでしょう${end(m[1])}${tail}`;
  }
  return sentence;
}

const EMPTY_VALUE_RE =
  /^(?:元記事(?:では|に)?)?(?:言及なし|記載なし|記載はなし|不明|案内なし|言及はなし|非公表|未公表|公式に記載なし|料金ページに記載なし|—|－|-|–)(?:（[^）]*）)?。?$/;
const TABLE_SEP_RE = /^\|?\s*:?-{2,}/;
const LIST_RE = /^\s*(?:[-*+]|\d+[.)])\s+/;
const DATE_NUM_RE = /\d+(?=\s*(?:年|月|日|時|分))/g;
const NUM_RE = /\d+(?:[.,]\d+)*/g;

export function splitSentences(line: string): string[] {
  const out: string[] = [];
  let buf = "";
  let depth = 0;
  for (const ch of line) {
    buf += ch;
    if ("「『（(".includes(ch)) depth += 1;
    else if ("」』）)".includes(ch) && depth > 0) depth -= 1;
    else if ("。！？".includes(ch) && depth === 0) {
      out.push(buf);
      buf = "";
    }
  }
  if (buf.trim()) out.push(buf);
  return out;
}

const hasFact = (text: string) => FACT_RE.test(text);
const isMeta = (s: string) => META_RE.test(s);
const isNonanswer = (s: string) => NONANSWER_RE.test(s);
const isFiller = (s: string) => FILLER_RE.test(s) && !hasFact(s);
const stripMarks = (s: string) => s.replace(/[*_`]/g, "");
const cells = (line: string) =>
  line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

const isEmptyValue = (cell: string) => EMPTY_VALUE_RE.test(stripMarks(cell));
const row = (cs: string[]) => `| ${cs.join(" | ")} |`;

function cleanTable(lines: string[]): string[] {
  if (lines.length < 2 || !TABLE_SEP_RE.test(lines[1].trim())) return lines;
  const header = cells(lines[0]);
  const sourceCol = header.length && /出典|ソース/.test(header[header.length - 1]) ? header.length - 1 : -1;
  const kept = lines.slice(0, 2);
  for (const line of lines.slice(2)) {
    const values = cells(line).filter((_, i) => i > 0 && i !== sourceCol);
    if (values.length && values.every(isEmptyValue)) continue;
    kept.push(line);
  }
  if (kept.length <= 2) return [];
  // 残った「記載なし」「—」のセルは空欄に、全部が空の列は列ごと消す（読者に「記載なし」を見せない。2026-11-03）
  const rows = kept.slice(2).map(cells);
  if (!rows.some((r) => r.slice(1).some(isEmptyValue))) return kept;
  const width = header.length;
  const keepCols: number[] = [];
  for (let i = 0; i < width; i++) {
    if (i > 0 && rows.every((r) => i >= r.length || isEmptyValue(r[i]))) continue;
    keepCols.push(i);
  }
  const sep = cells(kept[1]);
  const out = [row(keepCols.map((i) => header[i])), row(keepCols.map((i) => (i < sep.length ? sep[i] : "---")))];
  for (const r of rows) out.push(row(keepCols.map((i) => (i > 0 && i < r.length && isEmptyValue(r[i]) ? "" : i < r.length ? r[i] : ""))));
  return out;
}

/** 文の前の下ごしらえ: かっこ書きの「元記事では言及なし」を消し、表の注記の「『記載なし』」を「空欄」にする */
const tidy = (text: string) => text.replace(NONANSWER_PAREN_RE, "").replace(EMPTY_TOKEN_RE, "空欄");
const keepSentence = (s: string) => !isMeta(s) && !SOURCE_NONANSWER_RE.test(s);

function cleanListItem(line: string): string | null {
  const body = tidy(line.replace(LIST_RE, "").trim());
  const m = /^[^：:]{1,40}[：:]\s*(.+)$/.exec(stripMarks(body));
  if (m && EMPTY_VALUE_RE.test(m[1].trim())) return null;
  if (EMPTY_VALUE_RE.test(stripMarks(body))) return null;
  // 「注意点：〜」の見出し語は残し、値の文だけを整える
  const lm = /^([^：:。]{1,40}[：:]\s*)(.+)$/.exec(body);
  const [label, value] = lm ? [lm[1], lm[2]] : ["", body];
  const text = splitSentences(value).filter(keepSentence).map(professionalSentence).join("").trim();
  if (!text) return null;
  const indent = line.slice(0, line.length - line.trimStart().length);
  const marker = (LIST_RE.exec(line)?.[0] ?? "- ").trim();
  return `${indent}${marker} ${label}${text}`;
}

function cleanProse(line: string): string {
  return splitSentences(tidy(line))
    .filter((s) => keepSentence(s) && !isFiller(s))
    .map(professionalSentence)
    .join("")
    .trim();
}

const personaSentenceOk = (s: string) =>
  Boolean(s.trim()) && keepSentence(s) && !isNonanswer(s) && !FILLER_RE.test(s) && !PERSONA_FILLER_RE.test(s);

/** 立場の段落: 答えにならない文を除き、事実が残る段落だけ返す（残らなければ空） */
function personaParagraph(text: string): string {
  const kept = splitSentences(text).filter(personaSentenceOk).join("").trim();
  return hasFact(kept) ? kept : "";
}

/** 立場の小見出しの行を消し、その下（次の見出し・太字の行まで）は事実があって答えになっている段落だけ残す（台帳 A24） */
function dropPersona(lines: string[]): string[] {
  const out: string[] = [];
  let inside = false;
  for (const line of lines) {
    const stripped = line.trim();
    if (PERSONA_SUBHEAD_RE.test(stripped)) {
      inside = true;
      continue;
    }
    if (stripped.startsWith("#") || /^\*\*[^*]+\*\*$/.test(stripped)) inside = false;
    if (!inside || !stripped || stripped.startsWith("|")) {
      out.push(line);
      continue;
    }
    if (LIST_RE.test(line)) {
      if (personaParagraph(line.replace(LIST_RE, ""))) out.push(line);
      continue;
    }
    const kept = personaParagraph(stripped);
    if (kept) out.push(kept);
  }
  return out;
}

function cleanBlock(text: string): string {
  const lines = dropPersona(text.split("\n"));
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const stripped = line.trim();
    if (stripped.startsWith("|")) {
      const block: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) block.push(lines[i++]);
      out.push(...cleanTable(block));
      continue;
    }
    if (!stripped || /^(#|>|```|<)/.test(stripped) || /^!\[|^\[.*\]\(.*\)$/.test(stripped)) out.push(line);
    else if (LIST_RE.test(line)) {
      const item = cleanListItem(line);
      if (item !== null) out.push(item);
    } else {
      const cleaned = cleanProse(stripped);
      if (cleaned) out.push(cleaned);
    }
    i += 1;
  }
  const result: string[] = [];
  out.forEach((line, idx) => {
    if (line.startsWith("### ")) {
      const rest = out.slice(idx + 1);
      let next = rest.findIndex((x) => x.startsWith("### "));
      if (next < 0) next = rest.length;
      if (!rest.slice(0, next).some((x) => x.trim())) return;
    }
    result.push(line);
  });
  return result.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

/** 確かめ済みの比較表の章（scripts/rivals.py）。文は消さず、表の「公式に記載なし」を空欄にして注記の言い方をそろえるだけ */
function cleanDataTable(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    if (lines[i].trim().startsWith("|")) {
      const block: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) block.push(lines[i++]);
      out.push(...cleanTable(block));
      continue;
    }
    out.push(lines[i].replace(EMPTY_TOKEN_RE, "空欄"));
    i += 1;
  }
  return out.join("\n");
}

/** 判定のひとこと: 答えになっていない文を除き、呼びかけの文末をそろえる（scripts/section_gate.py の clean_take と同じ）。全部消えるなら空 */
export function cleanTake(take: string): string {
  return splitSentences(tidy(take ?? ""))
    .filter(keepSentence)
    .map(professionalSentence)
    .join("")
    .trim();
}

function proseSentences(text: string): string[] {
  const sentences: string[] = [];
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (!s || /^(#|\||>|```)/.test(s) || LIST_RE.test(line)) continue;
    sentences.push(...splitSentences(s).filter((x) => x.trim()));
  }
  return sentences;
}

function numericCells(text: string): number {
  let n = 0;
  for (const line of text.split("\n")) {
    const s = line.trim();
    if (s.startsWith("|") && !TABLE_SEP_RE.test(s)) n += cells(s).slice(1).filter((c) => /[0-9０-９]/.test(c)).length;
  }
  return n;
}

function plainNumbers(text: string): string[] {
  return text.normalize("NFKC").replace(DATE_NUM_RE, "").match(NUM_RE) ?? [];
}

export function sectionVerdict(heading: string, content: string): "keep" | "hide" {
  const h = (heading ?? "").trim();
  if (h === DATA_RIVAL_HEADING || PROTECTED_RE.test(h)) return "keep";
  const structured = content.split("\n").filter((l) => (LIST_RE.test(l) || l.trim().startsWith("|")) && l.trim());
  const structText = structured.join("\n");
  const sentences = proseSentences(content);
  const answers = sentences.filter((s) => !isNonanswer(s));
  const nonanswers = sentences.filter((s) => isNonanswer(s));
  const answerText = answers.join("");
  if (!answers.length && !structured.length) return "hide";
  if (RIVAL_RE.test(h)) {
    if (OUTDATED_MODEL_RE.test(content)) return "hide";
    return numericCells(content) >= 2 || plainNumbers(answerText).length >= 2 ? "keep" : "hide";
  }
  if (NUMBERS_RE.test(h)) return plainNumbers(`${answerText}\n${structText}`).length >= 2 ? "keep" : "hide";
  if (PRICE_HEADING_RE.test(h) && !PRICE_WORD_RE.test(answerText + structText)) return "hide";
  const factual = answers.filter(hasFact);
  if (nonanswers.length && !structured.length && nonanswers.length >= answers.length && factual.length <= 1) return "hide";
  return "keep";
}

export function splitSections(markdown: string): { heading: string | null; content: string }[] {
  const parts = (markdown ?? "").split(/^## /m);
  const out: { heading: string | null; content: string }[] = [];
  if (parts[0]?.trim()) out.push({ heading: null, content: parts[0] });
  for (const p of parts.slice(1)) {
    const nl = p.indexOf("\n");
    const head = nl < 0 ? p : p.slice(0, nl);
    out.push({ heading: head.trim(), content: nl < 0 ? "" : p.slice(nl + 1) });
  }
  return out;
}

/** 表があるか、事実のある答えの文・箇条書きが2つ以上あるか（見出しが「〜はない」でも中身がある章は隠さない） */
function substantive(content: string): boolean {
  const lines = content.split("\n");
  if (lines.some((l) => l.trim().startsWith("|"))) return true;
  const items = lines.filter((l) => LIST_RE.test(l)).map((l) => l.replace(LIST_RE, ""));
  return [...proseSentences(content), ...items].filter((x) => hasFact(x) && !isNonanswer(x)).length >= 2;
}

/**
 * 中身なしの章・つなぎの文を除いた Markdown と、隠した章の見出し。
 * titles は記事ごとの見出し（記事 JSON の sectionTitles の body か easy）。表示の見出しが「〜はない／未発表」の章は、
 * 中身（表か、事実のある答えが2つ以上）が無ければ隠す。中身があれば見出しだけ普通の言い方になる（usableTitle が弾く。台帳 A24）
 */
export function cleanSections(markdown: string, titles?: Record<string, string>): { markdown: string; hidden: string[] } {
  const hidden: string[] = [];
  const out: string[] = [];
  for (const { heading, content } of splitSections(markdown)) {
    if (heading === null) {
      out.push(content.trim());
      continue;
    }
    if (heading === DATA_RIVAL_HEADING) {
      out.push(`## ${heading}\n\n${cleanDataTable(content).trim()}`);
      continue;
    }
    const cleaned = cleanBlock(content);
    if (sectionVerdict(heading, cleaned) === "hide") {
      hidden.push(heading);
      continue;
    }
    const own = typeof titles?.[heading] === "string" ? titles[heading] : "";
    if ((isNonanswerTitle(own) || isNonanswerTitle(heading)) && !substantive(cleaned)) {
      hidden.push(heading);
      continue;
    }
    out.push(cleaned ? `## ${heading}\n\n${cleaned}` : `## ${heading}`);
  }
  const text = out.filter(Boolean).join("\n\n").replace(/\n{3,}/g, "\n\n").trim();
  return { markdown: text + ((markdown ?? "").endsWith("\n") ? "\n" : ""), hidden };
}

// ------------------------------------------------------------------ //
// 見出しの言い換え
// ------------------------------------------------------------------ //

/** 記事ごとの見出し（記事 JSON の sectionTitles。scripts/section_polish.py が作り、事実と長さを確かめて保存） */
export type SectionTitles = { body?: Record<string, string>; easy?: Record<string, string> };

/**
 * 記事ごとの見出しが無いときの、ニュースサイトの普通の見出し（決まった型の質問をそのまま出さない）。
 * キーは本文 JSON の章名。ここに無い章名（記事ごとに書かれた見出し）はそのまま出す
 */
const BODY_FALLBACK: Record<string, string> = {
  何が発表された: "発表の内容",
  "何が発表された？": "発表の内容",
  何が発表されたのか: "発表の内容",
  "いつ、何が発表された？": "発表の内容",
  "前と何が違う？": "従来からの変更点",
  "前と何が違う？注意点は？": "変更点と注意点",
  "従来との違い・注意点": "変更点と注意点",
  "中身をくわしく見ると？": "詳しい内容",
  "数字で見るとどう変わった？": "数字で見る変化",
  "数字で見ると？": "主な数字",
  "この数字はどう読む？": "数字の読み方",
  "ライバルと比べてどう？": "競合との比較",
  競合サービスとの比較: "競合との比較",
  [DATA_RIVAL_HEADING]: "同クラスの製品との比較",
  "料金は？": "料金",
  "料金・提供プラン": "料金とプラン",
  "料金と提供時期は？": "料金と提供時期",
  "日本で使える？": "日本での提供状況",
  日本から使う方法: "日本からの使い方",
  "自分にはどう関係する？": "影響を受ける人",
  "自分に関係ある？": "影響を受ける人",
  誰にどう効くのか: "仕事への影響",
  "もう使える？取引できる？": "提供時期と利用方法",
  "もう使える？申し込める？": "提供時期と申し込み方法",
  "もう使える？": "提供状況",
  "もう買える？使える？": "発売時期と入手方法",
  "もう買える？": "発売時期と入手方法",
  "もう申し込める？使える？": "申し込み方法",
  "もう申し込める？": "申し込み方法",
  "もう見られる？遊べる？参加できる？": "公開時期と参加方法",
  "なぜ今？業界への影響は？": "背景と業界への影響",
  "なぜ今？気をつけることは？": "背景と注意点",
  "スペックと価格は？": "仕様と価格",
  "どんな製品？": "製品の特徴",
  "どんな制度？": "制度の内容",
  "どこが見どころ？": "見どころ",
  "いつから、誰が、何をすればいい？": "対象と手続き",
  "いつから、どこで参加できる？": "日程と参加方法",
  "いつ、どこで楽しめる？": "日程と会場",
  "性能・ベンチマーク": "性能",
  "発表された機能・アップデートの全容": "新機能の内容",
  "発表された機能・内容の全体像": "新機能の内容",
  これまでの流れ: "これまでの経緯",
  横断して見えてくること: "並べて見えること",
  個別のポイント: "それぞれの要点",
  結局どう捉えるべきか: "どう受け止めるか",
  "まだ分からないことは？": "未発表の点",
};

const EASY_FALLBACK: Record<string, string> = {
  ひとことで言うと: "要点",
  ひとすることで言うと: "要点",
  "何がすごいの？": "何が新しいのか",
  "自分に関係ある？": "関係する人",
  使ってみるには: "利用を始めるには",
};

/** 保存された見出しとして使ってよいか（scripts/section_polish.py の valid_title と同じ規則の最小限） */
export function usableTitle(title: unknown): title is string {
  if (typeof title !== "string") return false;
  const t = title.trim();
  return t.length >= 2 && t.length <= 32 && !/[<>#\n|]/.test(t) && !/。$/.test(t) && !isNonanswerTitle(t);
}

export function displayHeading(heading: string, kind: "body" | "easy", titles?: SectionTitles): string {
  const own = titles?.[kind]?.[heading];
  if (usableTitle(own)) return own.trim();
  const fallback = kind === "easy" ? EASY_FALLBACK : BODY_FALLBACK;
  return fallback[heading] ?? heading;
}

const unescapeHtml = (s: string) =>
  s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'");
const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** 本文の章の id（目次のリンク先）。prefix で本文とかんたん版を分ける */
export const sectionId = (prefix: string, index: number) => `${prefix}-${index + 1}`;

/**
 * 描画済み HTML の <h2>章名</h2> を、表示用の見出しに置き換え、目次のリンク先の id を付ける。
 * 図・料金表・計算機の差し込み（章名で位置を探す）が終わってから呼ぶ
 */
export function retitleHtml(html: string, kind: "body" | "easy", titles?: SectionTitles, idPrefix = "sec"): string {
  let index = 0;
  return html.replace(/<h2>([\s\S]*?)<\/h2>/g, (_m, inner: string) => {
    const plain = unescapeHtml(inner.replace(/<[^>]+>/g, "")).trim();
    const display = displayHeading(plain, kind, titles);
    const id = sectionId(idPrefix, index++);
    return `<h2 id="${id}">${display === plain ? inner : escapeHtml(display)}</h2>`;
  });
}

/** 目次（表示用の見出しと id）。cleanSections 済みの Markdown から */
export function tocOf(markdown: string, titles?: SectionTitles, idPrefix = "sec"): { id: string; text: string }[] {
  return splitSections(markdown)
    .filter((s) => s.heading !== null)
    .map((s, i) => ({ id: sectionId(idPrefix, i), text: displayHeading(stripMarks(s.heading as string), "body", titles) }));
}

/** 要約の役目の章（「ひとことで言うと」「要点」）。上の「この記事のポイント」とくり返しになる（台帳 A28） */
export const SUMMARY_HEADING_RE = /^(?:ひとことで言うと|ひとすることで言うと|要点|この記事のポイント|ポイント|要約)[？?]?$/;

/** 要約の役目の章を除く（記事の上に「この記事のポイント」があるときに使う） */
export function dropSummarySections(markdown: string): string {
  return splitSections(markdown)
    .filter((s) => s.heading === null || !SUMMARY_HEADING_RE.test(s.heading.replace(/[*_`]/g, "").trim()))
    .map((s) => (s.heading === null ? s.content.trim() : `## ${s.heading}\n${s.content}`.trim()))
    .filter(Boolean)
    .join("\n\n");
}

