/**
 * メール配信の登録欄の置き場所（components/NewsletterSignup.tsx）。
 * 本文に自前の登録欄があるページ（記事の末尾・/newsletter）では、フッターの登録欄を出さない。
 * 同じ登録欄が1ページに2つ並ぶと押し売りに見えるため（2026-11-03 の点検。scripts/ui_check.mjs と owner_rules_audit が見張る）
 */
const OWN_SIGNUP: RegExp[] = [/^\/articles\/[^/]+\/?$/, /^\/newsletter\/?$/];

export function hasOwnSignup(pathname: string | null | undefined): boolean {
  const p = (pathname ?? "").split(/[?#]/)[0];
  return OWN_SIGNUP.some((re) => re.test(p));
}
