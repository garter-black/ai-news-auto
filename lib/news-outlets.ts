/**
 * 報道機関（ニュースサイト）の発信元か（2026-11-03 オーナー決定・台帳 W12）。
 *
 * 話題の見張り（ops/watchdog の radar）は、一次情報が無い話題では報道機関の記事を出典にする（scripts/web_articles.py が
 * channelSlug を radar-<報道機関のホスト> にする。報道機関の一覧の正本は scripts/fast_lane_rules.json の radar.outlets で、
 * scripts/images.py の MEDIA_HOSTS も同じものを使う。ずれは tests/test_news_outlets.cjs が確かめる）。
 * その発信元ページ（/channels/radar-itmedia.co.jp など）を「公式発表」と呼ぶと、報道を公式と偽ることになるうえ、
 * 「他社の記事をAIで書き直したページ」を検索に出すことになる。そこで報道機関の発信元は
 *   - 「公式発表」「公式ブログ・プレスリリース」「公式サイトを見る」と書かない（札は「報道」）
 *   - 検索に出さない（noindex）・サイトマップに入れない・足元の「情報源」に並べない
 * クライアントの部品（lib/companies.ts 経由の Thumb）からも読むので、大きな JSON は読み込まない。
 */

/** 話題の見張りで見つけ、公式の一次情報まで確かめた記事の発信元（Anthropic公式・SEC・NVIDIA公式などが混ざる） */
export const RADAR_OFFICIAL = "radar-official";
/** 話題のキーワード（複数の報道・公式をまとめた記事） */
export const TRENDS = "trends";

/** 報道機関の発信元か（radar-<報道機関のホスト>・話題のキーワード） */
export function isNewsOutletChannel(slug?: string | null): boolean {
  const s = (slug ?? "").trim();
  if (!s) return false;
  if (s === TRENDS) return true;
  // radar-<ホスト> は報道機関の記事が出典。公式の一次情報まで確かめた記事だけが radar-official
  return s.startsWith("radar-") && s !== RADAR_OFFICIAL;
}

/** 発信元ページを検索に出さない・サイトマップに入れない・足元に並べないか（報道機関と、各社が混ざった radar-official） */
export function isThinSourceChannel(slug?: string | null): boolean {
  return isNewsOutletChannel(slug) || slug === RADAR_OFFICIAL;
}

/** 発信元ページの名前（記事の channel の先頭のもの＝「米証券取引委員会（SEC）」などではなく、発信元の実態に合わせる） */
export function channelDisplayLabel(slug: string, label: string): string {
  if (slug === RADAR_OFFICIAL) return "各社・機関の公式発表（話題から）";
  if (slug === TRENDS) return "話題のニュース";
  return label.trim();
}

/** 発信元ページの題名の後ろ。報道機関は「発表」ではない */
export function channelTitleTail(channel: string): string {
  return isNewsOutletChannel(channel) ? "の報道をもとにした記事" : "の最新発表まとめ";
}

/**
 * 発信元ページの説明（meta description と本文の両方）。報道機関の発信元を「公式発表」「公式ブログ・プレスリリース」と呼ばない。
 * 社名のあとに半角の空白を入れない（「ブルームバーグ で公開された」になっていた。2026-11-03 の点検 #2）
 */
export function channelDescription(channel: string, label: string, web: boolean, count: number): string {
  if (channel === RADAR_OFFICIAL) {
    return `話題になったニュースのうち、企業・官公庁の公式発表や提出書類までさかのぼって確かめた記事 ${count} 本です。発表元は記事ごとに異なります。`;
  }
  if (channel === TRENDS) {
    return `話題になったキーワードを、複数の報道と公式の資料をもとにまとめた記事 ${count} 本です。出典は記事ごとに示しています。`;
  }
  if (isNewsOutletChannel(channel)) {
    return `${label}の報道をもとに、公式の資料が見つからなかった話題を日本語でまとめた記事 ${count} 本です。報道の内容は記事ごとに出典を示しています。`;
  }
  return web
    ? `${label}が公開した公式ブログ・プレスリリースをもとにした日本語の解説記事 ${count} 本です。`
    : `${label}のYouTubeチャンネルで公開された動画をもとにした日本語の解説記事 ${count} 本です。`;
}
