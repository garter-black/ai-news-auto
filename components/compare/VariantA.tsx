import {
  Breadcrumb,
  CompareTable,
  Disclosure,
  FaqList,
  GuideMeta,
  HowWeMade,
  OutLink,
  PrMark,
  PriceLine,
  ProductVisual,
  RelatedNews,
  SourceList,
  VerdictMark,
} from "./parts";
import type { GuideView } from "./view";
import { Phrases } from "@/lib/highlight";

/**
 * A：報道テロップ型。墨の帯と青の札で見出しを立て、判定は○△×の判子、表は密に。
 */
export default function VariantA({ view }: { view: GuideView }) {
  const { guide, products, personas } = view;
  return (
    <div className="relative mx-auto max-w-[1080px] px-4 py-6 sm:px-8 sm:py-10">
      <GuideMeta view={view} />
      <Breadcrumb />

      {/* 見出し帯 */}
      <header className="mt-5">
        <div className="flex items-stretch text-meta font-bold">
          <span className="bg-accent px-3 py-1.5 tracking-[0.12em] text-white">比較ガイド</span>
          <span className="flex-1 bg-ink px-3 py-1.5 text-paper">
            {guide.shortName}
            <span className="index-num ml-2 font-normal opacity-75">{products.length}件を比較</span>
          </span>
        </div>
        <h1 className="t-h1 mt-4"><Phrases text={guide.title} /></h1>
        <p className="index-num mt-3 border-y border-ink py-2 text-meta text-ink">
          <span className="font-semibold">{view.checkedLabel}</span>
          <span className="mx-2 text-line-strong">|</span>提携{products.filter((p) => p.sponsored).length}・提携なし
          {products.filter((p) => !p.sponsored).length}
        </p>
        <p className="t-lead mt-4">{guide.intro}</p>
        <Disclosure view={view} className="mt-3" />
      </header>

      {/* 結論 */}
      <section aria-labelledby="a-pick" className="mt-9">
        <Telop id="a-pick" label="結論" title="迷ったら、この組み合わせ" />
        <ol className="border-b-2 border-ink">
          {personas.map((persona, index) => (
            <li key={persona.who} className="grid gap-3 border-t border-line py-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] sm:gap-6">
              <div className="flex gap-3">
                <span className="index-num pt-0.5 text-meta font-semibold text-accent">{String(index + 1).padStart(2, "0")}</span>
                <p className="text-[16px] font-bold leading-snug text-ink">{persona.who}は</p>
              </div>
              <div className="pl-8 sm:pl-0">
                <p className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  {persona.products.map((product) => (
                    <a key={product.key} href={`#p-${product.key}`} className="group inline-flex items-center gap-2">
                      <ProductVisual product={product} compact className="size-10 shrink-0 rounded" />
                      <span className="text-[18px] font-black leading-tight text-ink group-hover:underline">{product.item.name}</span>
                      {product.sponsored && <PrMark className="text-muted" />}
                    </a>
                  ))}
                </p>
                <p className="mt-1.5 text-desc leading-[1.75] text-ink-soft">{persona.why}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      {/* サイソク判定 */}
      <section aria-labelledby="a-verdict" className="mt-12">
        <Telop id="a-verdict" label="判定" title="サイソク判定" note={`公式の記載を4項目に整理（点数はつけていません）・${view.checkedLabel}`} />
        <div className="grid gap-px overflow-hidden border border-ink bg-ink sm:grid-cols-2 lg:grid-cols-3">
          {products.map((product) =>
            product.verdict ? (
              <article key={product.key} className="bg-paper p-4">
                <h3 className="t-card flex items-center gap-2">
                  {product.item.name}
                  {product.sponsored && <PrMark className="text-muted" />}
                </h3>
                <dl className="mt-3 grid gap-2 text-meta">
                  <Stamp label="日本で使える" level={product.verdict.japan.level} text={product.verdict.japan.text} />
                  <Stamp label="無料で試せる" level={product.verdict.freeTrial.level} text={product.verdict.freeTrial.text} />
                  <div className="grid grid-cols-[6.5em_minmax(0,1fr)] items-baseline gap-2">
                    <dt className="font-bold text-muted">料金の目安</dt>
                    <dd className="font-bold tabular-nums text-ink">{product.verdict.cost}</dd>
                  </div>
                  <div className="grid grid-cols-[6.5em_minmax(0,1fr)] items-baseline gap-2">
                    <dt className="font-bold text-muted">向いている人</dt>
                    <dd className="text-ink">{product.verdict.bestFor}</dd>
                  </div>
                </dl>
              </article>
            ) : null,
          )}
        </div>
      </section>

      {/* 比較表 */}
      <section aria-labelledby="a-table" className="mt-12">
        <Telop id="a-table" label="一覧" title="比較表" note="9項目・公式サイトの記載どおり" />
        <CompareTable view={view} tone="news" />
      </section>

      {/* 選び方 */}
      <section aria-labelledby="a-how" className="mt-12">
        <Telop id="a-how" label="選び方" title="選ぶ前に決めておく5つのこと" />
        <ol className="grid gap-x-8 sm:grid-cols-2">
          {(guide.decisionPoints ?? []).map((point, index) => (
            <li key={point.title} className="border-t border-ink py-4">
              <p className="index-num text-[28px] font-semibold leading-none text-accent">{index + 1}</p>
              <h3 className="t-card mt-2 leading-snug">{point.title}</h3>
              <p className="mt-1.5 text-desc leading-[1.85] text-ink-soft">{point.body}</p>
            </li>
          ))}
        </ol>
      </section>

      {/* 商品ごと */}
      <section aria-labelledby="a-products" className="mt-12">
        <Telop id="a-products" label="詳細" title="1つずつ見る：できること・注意点" />
        <div className="grid gap-10">
          {products.map((product, index) => (
            <article key={product.key} id={`p-${product.key}`} className="scroll-mt-24 border-t-[3px] border-ink pt-4">
              <div className="grid gap-5 sm:grid-cols-[200px_minmax(0,1fr)]">
                <ProductVisual product={product} className="aspect-[4/3] w-full rounded-md border border-line sm:aspect-square" />
                <div className="min-w-0">
                  <p className="index-num text-meta text-muted">
                    {String(index + 1).padStart(2, "0")} ／ {product.category}
                  </p>
                  <h3 className="mt-1 flex flex-wrap items-center gap-2 text-[21px] font-black leading-snug text-ink">
                    {product.item.name}
                    {product.sponsored && <PrMark className="text-muted" />}
                  </h3>
                  <p className="mt-2 flex flex-wrap items-baseline gap-x-2">
                    <span className="index-num text-[22px] font-semibold text-ink">{product.price || "公式サイトで確認"}</span>
                    <PriceLine product={product} label={view.checkedLabel} />
                  </p>
                  <p className="mt-2 text-desc leading-[1.9] text-ink-soft">{product.summary}</p>
                </div>
              </div>
              <div className="mt-4 grid gap-5 sm:grid-cols-2">
                <ListBlock title="公式の記載でできること" items={product.strengths} mark="○" markClass="text-go" />
                <ListBlock title="注意点" items={product.limits} mark="△" markClass="text-muted" />
              </div>
              <div className="mt-4 flex flex-col gap-3 border-t border-line pt-3 sm:flex-row sm:items-center sm:justify-between">
                <SourceList product={product} />
                <OutLink
                  product={product}
                  className="flex h-11 shrink-0 items-center justify-center gap-1.5 rounded-md bg-ink px-5 text-note font-bold text-paper hover:opacity-85"
                >
                  {product.sponsored ? "公式ストアで見る" : "公式サイトで見る"}
                </OutLink>
              </div>
            </article>
          ))}
        </div>
      </section>

      {view.related.length > 0 && (
        <section aria-labelledby="a-news" className="mt-14">
          <Telop id="a-news" label="ニュース" title="最新ニュース" note="サイソクが報じた音声AI・文字起こしの関連記事" />
          <RelatedNews view={view} />
        </section>
      )}

      <section aria-labelledby="a-faq" className="mt-14">
        <Telop id="a-faq" label="FAQ" title="よくある質問" />
        <FaqList view={view} />
      </section>

      <section aria-labelledby="a-made" className="mt-12 border-t-2 border-ink pt-4">
        <h2 id="a-made" className="t-h3">このガイドの作り方</h2>
        <HowWeMade view={view} className="mt-2" />
      </section>
    </div>
  );
}

/** テロップ風の見出し：青の札＋墨の線 */
function Telop({ id, label, title, note }: { id: string; label: string; title: string; note?: string }) {
  return (
    <div className="mb-4">
      <div className="flex items-end gap-3 border-b-2 border-ink">
        <span className="bg-accent px-2 py-1 text-meta font-bold tracking-[0.1em] text-white">{label}</span>
        <h2 id={id} className="t-h2 pb-1">{title}</h2>
      </div>
      {note && <p className="mt-1.5 text-meta text-muted">{note}</p>}
    </div>
  );
}

function Stamp({ label, level, text }: { label: string; level: "yes" | "partial" | "no"; text: string }) {
  return (
    <div className="grid grid-cols-[6.5em_minmax(0,1fr)] items-start gap-2">
      <dt className="pt-0.5 font-bold text-muted">{label}</dt>
      <dd className="flex items-start gap-2 text-ink">
        <span
          className={`grid size-6 shrink-0 place-items-center rounded-full border-2 border-current text-meta leading-none ${
            level === "yes" ? "text-go" : level === "no" ? "text-live" : "text-ink-soft"
          }`}
        >
          <VerdictMark level={level} className="leading-none" />
        </span>
        <span className="pt-0.5">{text}</span>
      </dd>
    </div>
  );
}

function ListBlock({ title, items, mark, markClass }: { title: string; items: string[]; mark: string; markClass: string }) {
  return (
    <div>
      <h4 className="text-meta font-black text-ink">{title}</h4>
      <ul className="mt-2 grid gap-1.5 text-desc leading-[1.75] text-ink-soft">
        {items.map((text) => (
          <li key={text} className="grid grid-cols-[1.1rem_1fr] gap-1">
            <span aria-hidden className={markClass}>{mark}</span>
            {text}
          </li>
        ))}
      </ul>
    </div>
  );
}
