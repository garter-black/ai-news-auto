/**
 * 記事の「よくある質問」（3〜5問）。答えは本文・元記事に書かれた事実だけ（scripts/web_articles.py の faq、品質ゲートで事実確認）。
 * 同じ問いと答えを記事ページの FAQPage の構造化データにも使う（表示している内容と一致させる）。
 */
export default function ArticleFaq({ items }: { items: { q: string; a: string }[] }) {
  return (
    <section aria-labelledby="article-faq-heading" className="mt-12 max-w-[40em]">
      <h2 id="article-faq-heading" className="t-h2">
        よくある質問
      </h2>
      <dl className="mt-4 divide-y divide-line overflow-hidden rounded-2xl border border-line">
        {items.map((item, index) => (
          <div key={index} className="px-5 py-4">
            <dt className="text-desc font-bold leading-snug text-ink">Q. {item.q}</dt>
            <dd className="mt-1.5 text-desc leading-[1.75] text-ink-soft">{item.a}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
