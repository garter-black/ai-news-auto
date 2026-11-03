import type { Metadata } from "next";
import Link from "next/link";

// 404 の題名がトップと同じだと、タブや履歴で見分けがつかない（noindex は Next.js が自動で付ける）
export const metadata: Metadata = {
  title: "ページが見つかりませんでした",
};

// 行き止まりにしないよう、よく使われる入口を並べる
const LINKS = [
  { href: "/articles", label: "新着記事の一覧" },
  { href: "/news", label: "ビジネス・お金・暮らしのニュース" },
  { href: "/guides", label: "AIの使い方ガイド" },
  { href: "/ai-pricing", label: "AIの料金・日本対応 早見表" },
];

export default function NotFound() {
  return (
    <div className="mx-auto grid max-w-3xl place-items-center px-4 py-32 text-center">
      <p className="text-6xl font-black text-accent">404</p>
      <h1 className="mt-4 text-xl font-bold text-ink">
        ページが見つかりませんでした
      </h1>
      <p className="mt-2 text-note text-muted">
        記事が削除されたか、URLが変更された可能性があります。
      </p>
      <ul className="mt-6 flex flex-wrap justify-center gap-x-5 gap-y-2 text-note">
        {LINKS.map((link) => (
          <li key={link.href}>
            <Link href={link.href} className="font-semibold text-accent underline underline-offset-4 hover:text-ink">
              {link.label}
            </Link>
          </li>
        ))}
      </ul>
      <Link
        href="/"
        className="mt-8 rounded-md bg-ink px-6 py-3 text-note font-semibold text-paper transition-opacity hover:opacity-85"
      >
        トップへ戻る
      </Link>
    </div>
  );
}
