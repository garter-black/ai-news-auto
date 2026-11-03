/**
 * 一覧・カードに出す見出し。40字を超える見出しは、最初の区切り（全角の空白・－・｜、日本語のすぐ後ろの半角空白）までの前半だけを出す。
 * 記事ページの見出し（h1）・検索用の題名（seoTitle）・構造化データは全文のまま。
 * 区切りは scripts/headline_normalize.py が全角の空白にそろえている。品質ゲートも40字超を警告する（quality.title_warnings）。
 */
export const DISPLAY_MAX = 40;

/** 最初の区切りまでの前半。短すぎる（12字未満）・かっこが閉じていない・区切りが無いときは null */
export function headOf(title: string): string | null {
  const text = title.trim();
  // 「Agent Compute Engine」の中のような英字どうしの間の空白では切らない
  const head = text.split(/(?<=[^\x00-\x7f])[ 　]|[　－―—｜|]/)[0].trim();
  const balanced = (head.match(/[『「（(]/g) ?? []).length === (head.match(/[』」）)]/g) ?? []).length;
  return head.length >= 12 && head.length < text.length && balanced ? head : null;
}

/** 一覧・カード用の見出し（40字以内はそのまま） */
export function displayHeadline(title: string): string {
  const text = title.trim();
  return text.length > DISPLAY_MAX ? (headOf(text) ?? text) : text;
}

/**
 * 見出しを本見出しと補足（最初の全角の空白のあと）に分ける（2026-11-03 オーナー指摘: 補足は意図した改行として別の行に出す）。
 * かっこの中の空白・前半が短すぎる（8字未満）・後半が短すぎる（3字未満）ときは分けない。
 */
export function splitHeadline(title: string): { main: string; sub: string | null } {
  const text = title.trim();
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if ("「『（(".includes(ch)) depth++;
    else if ("」』）)".includes(ch) && depth > 0) depth--;
    else if (ch === "　" && depth === 0) {
      const main = text.slice(0, i).trim();
      const sub = text.slice(i + 1).trim();
      if (main.length >= 8 && sub.length >= 3) return { main, sub };
      return { main: text, sub: null };
    }
  }
  return { main: text, sub: null };
}


// ------------------------------------------------------------------ //
// 題名の門番（scripts/headline_normalize.py の strip_known_descriptors と同じ）。朝のまとめ・週のまとめ・その画像の題名にも通す（台帳 A25）
// ------------------------------------------------------------------ //

/** 説明を付けない会社（headline_normalize.WELL_KNOWN_COMPANIES と同じ。長い名前が先。tests/test_digest_titles.py が照らし合わせる） */
export const WELL_KNOWN_COMPANIES: readonly string[] = ["ソフトバンクグループ", "Salesforce", "Microsoft", "Anthropic", "Qualcomm", "DeepSeek", "Netflix", "YouTube", "Samsung", "Spotify", "LINEヤフー", "ソニーグループ", "OpenAI", "Amazon", "本田技研工業", "Google", "Oracle", "楽天グループ", "NVIDIA", "トヨタ自動車", "パナソニック", "Toyota", "サムスン電子", "ソフトバンク", "NTTドコモ", "Apple", "Intel", "日産自動車", "Honda", "Adobe", "Tesla", "Canva", "Sony", "Dell", "KDDI", "Meta", "サムスン", "TSMC", "Uber", "テスラ", "任天堂", "ソニー", "NTT", "AMD", "Arm", "IBM", "トヨタ", "ホンダ", "xAI", "AWS", "日産", "楽天"];
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const COMPANY_ALT = WELL_KNOWN_COMPANIES.map(escapeRe).join("|");
// 「ChatGPTのOpenAIが」「AI半導体のNVIDIA、」。説明は見出しの頭か区切り（空白・読点）の直後から
const DESCRIPTOR_RE = new RegExp(`(^|[　、 ])([^\\s　、。「」『』（）()]{1,14}?)の(${COMPANY_ALT})(?![A-Za-z0-9ァ-ヴー])(?=[がはもとの、　]|$)`, "g");
// 説明ではなく関係を表す言葉（「競合のGoogle」「傘下のAWS」は外さない）
const RELATION_RE = /(競合|傘下|親会社|子会社|同業|ライバル|提携先|顧客|取引先|株主|相手|大株主|元|前|旧|同じく)$/;

/** よく知られた会社の前の説明を外す。「ChatGPTのOpenAIが」→「OpenAIが」 */
export function stripKnownDescriptors(title: string): string {
  const matches = [...(title ?? "").matchAll(DESCRIPTOR_RE)].filter((m) => !RELATION_RE.test(m[2]));
  let out = title ?? "";
  for (const m of matches.reverse()) {
    const start = m.index ?? 0;
    out = out.slice(0, start) + m[1] + m[3] + out.slice(start + m[0].length);
  }
  return out;
}

/** 検索向けの問いの題名（「〇〇とは？日本で使える？料金も」「〜の買い方」）。まとめでは並べない（記事の見出し h1 を使う） */
export const SEO_QUESTION_RE = /とは[？?]|日本で(?:も)?使える[？?]|の買い方|使い方と料金|料金も$|料金と提供状況/;

/**
 * まとめ・画像に出す題名を門番に通す（台帳 A25）。説明つきの社名を外し、検索向けの問いの題名なら記事の見出し（newsTitle）に替える
 */
export function gateHeadline(text: string, newsTitle?: string): string {
  let t = stripKnownDescriptors((text ?? "").trim());
  if (newsTitle && (!t || SEO_QUESTION_RE.test(t))) t = stripKnownDescriptors(newsTitle.trim());
  return t;
}
