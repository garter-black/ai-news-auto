import type { Metadata } from "next";
import { withPageShare } from "@/lib/page-share";
import LegalPage from "@/components/LegalPage";
import { getAllArticles, getAllGenres } from "@/lib/articles";
import { siteConfig } from "@/site.config";

export const metadata: Metadata = withPageShare("about", "/about", {
  title: "運営者情報・編集方針",
  description: `${siteConfig.name}の運営者情報と、記事の制作方法・編集方針・AI利用の開示について説明しています。`,
  alternates: { canonical: "/about" },
});

export default function AboutPage() {
  const articles = getAllArticles();
  const genres = getAllGenres();
  const { operator } = siteConfig;

  return (
    <LegalPage
      eyebrow="ABOUT"
      title="運営者情報・編集方針"
      lead={`${siteConfig.siteName}（略称：${siteConfig.nickname}）は、公式発表をもとに、AI・ビジネス・お金・投資・不動産などの新しい動きと「自分にどう関係するか・何をすればいいか」を日本語で伝えるニュースメディアです。制作にはAIを使い、情報源と確認方法を開示しています。`}
      updatedAt="2026年11月2日"
    >
      <h2>運営者情報</h2>
      <table>
        <tbody>
          <tr>
            <th>サイト名</th>
            <td>{siteConfig.siteName}</td>
          </tr>
          <tr>
            <th>略称</th>
            <td>{siteConfig.nickname}</td>
          </tr>
          <tr>
            <th>旧サイト名</th>
            <td>{siteConfig.formerName}（2026年11月に改名）</td>
          </tr>
          <tr>
            <th>公式サイト</th>
            <td><a href="https://ai-tech-express.jp">https://ai-tech-express.jp</a></td>
          </tr>
          <tr>
            <th>運営者</th>
            <td>{operator.name}</td>
          </tr>
          <tr>
            <th>運営形態</th>
            <td>個人運営</td>
          </tr>
          <tr>
            <th>運営・編集責任者</th>
            <td>{operator.representative}</td>
          </tr>
          <tr>
            <th>所在地</th>
            <td>{operator.location}</td>
          </tr>
          <tr>
            <th>連絡先</th>
            <td>{operator.email}</td>
          </tr>
          <tr>
            <th>開設</th>
            <td>{operator.established}</td>
          </tr>
        </tbody>
      </table>
      <p>
        旧URL「ai-news-auto.vercel.app」から、現在の公式サイト「ai-tech-express.jp」へ移転しました。
        サイソクの記事・運営情報は、この公式サイトで公開しています。
      </p>

      <h2>このサイトが提供するもの</h2>
      <p>
        {siteConfig.tagline}
        {siteConfig.siteName}は、忙しく働きながら、仕事の成果や副収入・独立・資産づくりも考えている人のためのニュースサイトです。
      </p>
      <p>
        AI・テックの新発表、ビジネス・お金・投資、不動産、起業・副業、税金や補助金などの制度について、公式発表などの一次情報をもとに、
        要点を3行で整理し、「仕事・お金にどう活かせるか」「明日から何ができるか」まで書きます。
        海外AI企業の英語の発表は、日本から使えるか・料金はいくらか・無料で試せるかも判定しています。
        日本のスポーツの大きなニュースも扱います。
      </p>
      <p>
        根拠のない儲け話や、あおる見出しは書きません。
        特定の銘柄や物件の売買をすすめる投資助言は行わず、一般的な情報と判断材料の提示にとどめます。
      </p>
      <p>
        現在
        {genres.map((g, i) => (
          <span key={g.slug}>
            {i > 0 && "・"}
            <strong>{g.label}</strong>（{g.count}本）
          </span>
        ))}
        の計 {articles.length} 本を公開中です。記事は毎日追加しています。
      </p>

      <h2>速さと時刻の考え方</h2>
      <p>
        発表を早く見つけ、読者が判断に使える情報を早く届けることを目指しています。
        速さを確かめる際は、「公式の発表」「新着の検知」「記事の生成」「サイトでの公開確認」を分けて記録します。
        記事の生成が終わってから公開されるまでにも、確認や配信の時間がかかります。
      </p>
      <p>
        発表元の日時と、サイソクが記事を作成・更新した日時は別のものです。
        発表元が日付しか示していない場合は、発表から何分かを推定して実績に含めません。
        すべての発表を一定の分数以内に掲載すると約束するものではありません。
      </p>
      <h3>発表から公開確認までの実例</h3>
      <p>
        公式ページにある発表日時と、サイソクの記事URLが正常な応答（HTTP 200）を返した初回確認日時を比較しました。
        2026年10月31日に照合した3件の実例で、時刻は日本時間です。
      </p>
      <table>
        <thead>
          <tr>
            <th>発表・記事</th>
            <th>公式発表 → 公開確認</th>
            <th>所要時間</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>
              <a href="/articles/gemini-4-carbon-cybersecurity-model">Gemini 4 Carbon</a>
              <br />
              <a href="https://blog.google/innovation-and-ai/models-and-research/gemini-models/introducing-gemini-4-carbon/">Google公式発表</a>
            </td>
            <td>
              <time dateTime="2026-10-28T17:00:00+00:00">10月29日 02:00:00</time>
              <br />→ <time dateTime="2026-10-28T17:05:17+00:00">02:05:17</time>
            </td>
            <td>5分17秒</td>
          </tr>
          <tr>
            <td>
              <a href="/articles/gemini-notebook-free-textbooks">Gemini Notebookの教科書アクセス</a>
              <br />
              <a href="https://blog.google/innovation-and-ai/products/gemini-notebook/new-access-textbooks/">Google公式発表</a>
            </td>
            <td>
              <time dateTime="2026-10-29T16:00:00+00:00">10月30日 01:00:00</time>
              <br />→ <time dateTime="2026-10-29T16:07:24+00:00">01:07:24</time>
            </td>
            <td>7分24秒</td>
          </tr>
          <tr>
            <td>
              <a href="/articles/gemini-edit-files-directly">Geminiのファイル直接編集</a>
              <br />
              <a href="https://blog.google/innovation-and-ai/products/gemini-app/edit-files-directly/">Google公式発表</a>
            </td>
            <td>
              <time dateTime="2026-10-29T16:15:00+00:00">10月30日 01:15:00</time>
              <br />→ <time dateTime="2026-10-29T16:23:20+00:00">01:23:20</time>
            </td>
            <td>8分20秒</td>
          </tr>
        </tbody>
      </table>
      <p>
        公開確認は実際の公開より遅れる場合があり、本文全体の検証完了時刻を示すものではありません。
        検知・生成だけの所要時間や、全記事の平均・最速値、他媒体との速度比較とは区別しています。
      </p>

      <h2>記事の制作方法（AI利用の開示）</h2>
      <p>
        本サイトの記事は、<strong>生成AI（Google Gemini、Anthropic Claude ほか）を使って作成しています</strong>。
        人間のライターが一本ずつ執筆・全文校閲しているわけではありません。主な工程は次のとおりです。
      </p>
      <ol>
        <li>企業・研究機関・官公庁の公式発表、発表動画、プレスリリースなどの公開情報から、新着の発表を検出します。</li>
        <li>元の発表の本文（動画の場合は映像と音声）を生成AIに読ませ、編集方針に沿って日本語の記事に再構成させます。</li>
        <li>書き上げた記事を別の工程で元の発表と照らし合わせ、元にない数字・日付・固有名詞が含まれていないかを確認します。基準に届かない記事は公開しません。</li>
        <li>各記事の末尾に、元の発表へのリンクと、使ったAIモデルの名前を表示します。</li>
      </ol>
      <p>
        詳しい手順と事実確認のルールは<a href="/editorial-policy">編集方針・AI利用と事実確認について</a>で開示しています。
      </p>

      <h2>編集方針（要約）</h2>
      <ul>
        <li>
          <strong>一次情報を根拠にする。</strong>{" "}
          記事の中心となる事実は、元の発表で確認できた内容に限ります。
          背景説明のために外部知識を補う場合は「編集部注」と明記します。
        </li>
        <li>
          <strong>数字を省略しない。</strong>{" "}
          価格・ベンチマークスコアなどは、提示されたまま転記します。
          提示がなかった場合は「記載なし」と明記し、推測で埋めません。
        </li>
        <li>
          <strong>煽らない。</strong>{" "}
          「衝撃」「ヤバい」といった内容のない誇張表現、断定的な将来予測、
          読者を急かす表現は使用しません。
        </li>
        <li>
          <strong>不利な情報も書く。</strong>{" "}
          リスク・制約・競合に劣る点は、確認できた範囲で必ず記載します。
        </li>
        <li>
          <strong>出典に必ず戻れるようにする。</strong>{" "}
          全記事に元の発表（公式ブログ・プレスリリース・発表動画など）へのリンクを掲載しています。
        </li>
      </ul>

      <h2>限界と、お願い</h2>
      <p>
        生成AIによる自動生成である以上、<strong>誤りが含まれる可能性があります</strong>。
        数値の取り違えや文脈の誤解が起こりえます。
        重要な判断（購入・契約・投資・技術選定）を行う際は、
        必ず記事に併記した元の発表と、各社の公式情報をご自身で確認してください。
      </p>
      <p>
        誤りを見つけられた場合は、
        <a href="/contact">お問い合わせ</a>
        からご連絡ください。確認のうえ、訂正または記事の取り下げを行います。
        これまでの訂正は<a href="/corrections">訂正履歴</a>で公開しています。
        権利者の方からの削除・修正のご要望にも同様に対応します。
      </p>

      <h2>収益について</h2>
      <p>
        本サイトは、広告（Google AdSense）およびアフィリエイトプログラムによる収益で運営しています。
        詳細は<a href="/disclaimer">免責事項・広告について</a>および
        <a href="/privacy">プライバシーポリシー</a>をご覧ください。
        広告主から報酬を受け取っていることが、記事内の評価に影響しないよう、
        記事本文は広告の有無と切り離して生成しています。
      </p>
    </LegalPage>
  );
}
