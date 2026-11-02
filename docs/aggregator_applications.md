# ニュースアプリ・Google ニュース 申請資料

作成: 2026-10-27（2026-11-02 に媒体名を「サイソクAIニュース」から「サイソクニュース」へ変更）。サイト: https://ai-tech-express.jp（サイソクニュース）

各社の条件は公式ヘルプで確認した内容です（出典 URL は各節に記載）。
`【要記入】` は運営者本人にしか書けない項目です。実在しない情報は書かないでください。

---

## 0. 結論（どこに出すか）

| 配信先 | 個人で申請できるか | 今の見込み | 今日やること |
|---|---|---|---|
| **NewsPicks** | できる（「個人の場合は個人名」と明記） | 許諾は通りやすい。ただし露出は編集部・ユーザー次第 | **申請する** |
| **Google ニュース / Discover** | 申請の窓口がない（自動で対象） | 品質と透明性しだい | Search Console でニュースサイトマップを送信 |
| **グノシー** | できる（「法人・個人などで契約形態が異なる」） | 低〜中。PV と執筆体制、独自記事の割合を見られる | 申請してよい（下の文面で） |
| **スマートニュース** | **できない**（媒体ガイドライン 1-1「媒体運営者が法人である」） | 法人化するまでほぼゼロ | 申請しない。フィードだけ用意済み |
| **LINE NEWS** | **事実上できない**（フォームが法人名・代表者・編集体制を前提） | ほぼゼロ | 申請しない |
| Yahoo!ニュース | 新規受付停止中 | ― | ― |

各社が共通して嫌うもの: プレスリリースの言い換えが中心の媒体、出典の不明確な記事、宣伝目的の記事、運営者がわからない媒体。
直近14日の117本のうち、プレスリリース配信が元の記事は44本（約4割）あります。配信用フィードには、検索に出している分量のある記事（lib/indexing.ts）だけを載せています。

---

## 1. 用意したフィード

| URL | 用途 | 中身 |
|---|---|---|
| https://ai-tech-express.jp/feeds/smartnews.xml | スマートニュース（SmartFormat） | 全文・`media:thumbnail`（1200×630 JPEG）・`snf:logo`・`dc:creator`。直近14日・最大50本・1MB以内 |
| https://ai-tech-express.jp/feeds/gunosy.xml | グノシー（GunosyFeed 3.2） | 全文。表は段落に展開、本文中のリンクは外して出典リンクだけ残す。`guid` は URL でない値。直近10日・最大50本 |
| https://ai-tech-express.jp/feeds/news.xml | 汎用（NewsPicks・LINE NEWS など仕様非公開の配信先） | 全文・`enclosure`・`media:content`。直近14日・最大50本 |
| https://ai-tech-express.jp/feed.xml | 読者・RSS リーダー向け（従来どおり） | 要約のみ・全記事50本 |
| https://ai-tech-express.jp/news-sitemap.xml | Google ニュース | 直近48時間・媒体名「サイソクニュース」・言語 ja |

ロゴ画像:
- 横長 600×100: https://ai-tech-express.jp/feeds/logo-wide.png
- 横長 300×44: https://ai-tech-express.jp/feeds/logo-44.png
- 正方形 512×512: https://ai-tech-express.jp/icon

全フィードの本文の末尾に、出典リンクと「生成AIを使って作成し、元の発表と照合してから公開」という開示文（編集方針へのリンク）を入れています。広告・アフィリエイトリンク・スクリプトは入れていません。

---

## 2. 共通の文面（コピーして使う）

**媒体名**
サイソクニュース

**媒体 URL**
https://ai-tech-express.jp

**運営者**
サイソク編集部（個人運営）／運営責任者: 【要記入: 本名】

**連絡先**
koyuiw7@gmail.com（お問い合わせページ: https://ai-tech-express.jp/contact ）

**サイト公開**
2026年7月（独自ドメインは 2026年7月30日から）

**媒体概要（約200字）**
> サイソクニュースは、OpenAI・Google・Anthropicなど海外AI企業の新発表を、最速で日本語に整理して届けるニュースサイトです。公式ブログや発表動画などの一次情報をもとに、要点・料金・日本から使えるか・無料で試せるかを判定し、専門用語を使わない「かんたん版」もあわせて掲載します。記事の作成には生成AIを使い、元の発表との照合を経て公開しています。全記事に出典へのリンクを明記しています。

**編集方針の要約**
> ・企業・研究機関・官公庁の公式発表などの一次情報を根拠にし、全記事に出典へのリンクを載せます。
> ・記事の作成に生成AI（Google Gemini、Anthropic Claude ほか）を使っていることを開示し、各記事に使ったモデル名を表示しています。
> ・公開前に、記事を元の発表と照らし合わせ、元にない数字・日付・固有名詞が含まれていないかを確認します。基準に届かない記事は公開しません。
> ・誇張や煽りの表現を使わず、制約や不利な点も書きます。
> ・誤りは確認のうえ速やかに訂正し、記事内に訂正の日付と内容を記します。
> ・広告には「PR」と表示し、記事の内容と広告は切り離しています。
> 詳細: https://ai-tech-express.jp/editorial-policy

**更新頻度**
毎日。直近14日は117本（1日平均約8本）。うちプレスリリース配信を元にしたものを除くと1日約5本。

**ジャンル**
テクノロジー（AI・生成AI・IT サービス）。一部ビジネス・ガジェット。

**アクセス数（正直な数字）**
1日あたりの訪問者は約20〜30人（2026年10月時点、増加中）。月間の訪問者はおよそ600〜900人。
月間PV: 【要記入: Cloudflare Web Analytics の過去30日の数値】
（水増しした数字を書くと、審査で実数と照合されたときに信用を失います）

**他の媒体との違い**
> 発表ごとに「今すぐ使えるか」「日本から使えるか」「無料で試せるか」「料金」を判定したカードを記事の冒頭に置き、専門用語を使わない「かんたん版」に切り替えて読めるようにしています。英語の発表を読む時間がない人が、自分に関係あるかを数秒で判断できることを目指しています。

**記事の制作体制**
> 編集責任者1名（個人）。記事の下書きは生成AIが作成し、元の発表との照合（自動）を経て公開しています。【要記入: 人が公開前に目を通しているかどうか、正直に。通していないなら「人による個別の確認は行っていない」と書く】

**推薦記事（分量があり、AI ジャンルの代表的なもの）**
- https://ai-tech-express.jp/articles/openai-gpt-6-1-terra-announcement
- https://ai-tech-express.jp/articles/gpt-6-1-terra-sol-astra-comparison
- https://ai-tech-express.jp/articles/sullivan-cromwell-openai-codex-legal-tool
- 【要選定: 残り2本。プレスリリース由来でない、AI の記事から】

---

## 3. NewsPicks（今日申請する）

- 案内ページ: https://newspicks.zendesk.com/hc/ja/articles/29319272960281
- 申請フォーム: 「メディア様向け NewsPicks編成許諾申請フォーム」 https://docs.google.com/forms/d/e/1FAIpQLSffhqIPwjrnYJ8Jr8ZxT_-DQiaC-5YPXo9fdZbb5_SjzBZoXw/viewform
- 無償。「全てのメディア様の記事を掲載するとは限りません」

| 項目 | 記入内容 |
|---|---|
| メールアドレス | koyuiw7@gmail.com |
| 記事利用への同意 | 内容を読んで同意（タイトル・サムネイル・リンクの表示、タイトルの調整など） |
| RSS 配信の希望 | 希望する → https://ai-tech-express.jp/feeds/news.xml |
| 媒体名 | サイソクニュース |
| 媒体 URL | https://ai-tech-express.jp |
| 会社名（個人の場合は個人名） | 【要記入: 本名】 |
| 問い合わせページ URL | https://ai-tech-express.jp/contact （Web フォーム推奨とあるので、Google フォームを作ってこのページに置くとよい） |
| 担当者メールアドレス | koyuiw7@gmail.com |

---

## 4. Google ニュース / Google Discover（申請なし）

- 2024年4月から Publisher Center で媒体を追加できなくなり、2025年3月から媒体ページは自動生成に切り替わりました。**申請窓口はありません。** コンテンツポリシーに沿っていれば自動で対象になります。
  - https://support.google.com/news/publisher-center/answer/15898024
  - ポリシー: https://support.google.com/news/publisher-center/answer/6204050
- 透明性で求められるもの: 日付と署名、著者・媒体・発行者の情報、運営元の情報、連絡先。アフィリエイト関係の明示。
- ニュースサイトマップ: 直近2日・最大1000件・`news:name` は Google ニュース上の媒体名と完全一致。https://developers.google.com/search/docs/crawling-indexing/sitemaps/news-sitemap
- Discover: 幅1200px以上の画像と `max-image-preview:large`、内容を表すタイトル、釣りタイトルを避ける。https://developers.google.com/search/docs/appearance/google-discover
- 生成 AI: 価値を足さずに大量生成するとスパム扱いになり得る。人による事実確認と、どう作ったかの開示を推奨。https://developers.google.com/search/docs/fundamentals/using-gen-ai-content

サイト側で済んでいること:
- ニュースサイトマップ（媒体名を「サイソクニュース」に統一）・robots.txt に記載
- 記事の NewsArticle 構造化データ（見出し・掲載日時・更新日時・1200×630 画像・著者・発行者とロゴ・無料記事・編集方針と訂正方針・AI 利用の開示 `backstory`）
- `max-image-preview:large`
- 運営者情報・お問い合わせ・編集方針ページ、記事末尾の AI 利用の開示

運営者がやること: Search Console で `https://ai-tech-express.jp/news-sitemap.xml` を送信（未送信なら）。

---

## 5. グノシー（申請してよい）

- 申請先: https://gunosy.co.jp/contact/ で「コンテンツパートナー申込み（※審査がございます）」を選ぶ
- 審査は通常1〜2週間。**通過したときだけ連絡が来ます**（理由は非開示）。https://support.gunosy.link/hc/ja/articles/5579670880399
- フィード仕様: https://support.gunosy.link/hc/ja/articles/5533509025935 （検証ツールでエラーがないことを確認してから申請）
- 掲載ガイドライン: https://support.gunosy.link/hc/ja/articles/5533491683983 （本文のリンクは原則1つ。アフィリエイト不可）

| 項目 | 記入内容 |
|---|---|
| 会社名 | 【要記入: 法人がなければ個人名。個人である旨を明記】 |
| 担当者 | 【要記入: 本名】 |
| 住所 | 【要記入】 |
| 電話番号 | 【要記入】 |
| サイト URL | https://ai-tech-express.jp |
| サイト名 | サイソクニュース |
| 月間 PV / 月間 UU | 【要記入: 実数】／約600〜900 |
| 1日の配信本数（広告・プレスリリース転載・他社転載を除く） | 約5本 |
| 主な配信先 | なし（自社サイトのみ）。NewsPicks に申請中、と書けるなら書く |
| 記事執筆体制 | 上の「記事の制作体制」をそのまま |
| 他媒体との違い | 上の「他の媒体との違い」をそのまま |
| 引用している URL | 記事末尾に出典として元の発表（各社公式ブログ・プレスリリース・YouTube 公式チャンネル）へのリンクを記載 |
| RSS | https://ai-tech-express.jp/feeds/gunosy.xml |

通過後に求められるもの: 連携申込書（DocuSign）、RSS の URL を media@gunosy.com へ送付、チャンネル用バナー 750×420px（未作成）。

---

## 6. スマートニュース（今は申請しない）

- 入口: https://publishers.smartnews.com/hc/ja/articles/360010888174
- 手順1 事前確認フォーム: https://airtable.com/appz09FBNuCKg9R9D/pagfs274TtH2xBBzm/form （見送りの場合は返信なし）
- 手順2 SmartFormat 申請フォーム（事前確認を通った媒体にだけ案内）
- **媒体ガイドライン 1-1「媒体運営者が法人である」**。個人運営の媒体は受け付けていません。https://publishers.smartnews.com/hc/ja/articles/5153479551385
- ほかに、法人名と代表者名のサイト上の明記（1-2）、プレスリリースや動画への過度な依存の制限（2-2）。
- SmartFormat 仕様: https://publishers.smartnews.com/hc/ja/articles/360010977813

法人化したときの申請項目: SmartFormat の URL（https://ai-tech-express.jp/feeds/smartnews.xml ）、媒体名、媒体 URL、担当者名、メールアドレス、運営社名、運営社情報の URL（https://ai-tech-express.jp/about ）、代表者名、運営者の住所。

フィードについて: `snf:relatedLink` は Media Prosper Program の参加媒体だけが使えるので入れていません。`snf:analytics` は GA4 などの計測タグ用です。サイトは GA を使っていないので入れていません（GA4 を入れたら追加できます）。

---

## 7. LINE NEWS（今は申請しない）

- エントリーフォーム: https://form-business.yahoo.co.jp/claris/enqueteForm?inquiry_type=yntlmediaform
- フォームの必須項目: 運営会社名（法人名）、代表者名、住所、会社概要 URL、編集方針、編集責任者の氏名と経歴、編集者・ライターの人数、校閲・ファクトチェックを含む制作フロー、推薦記事5本など。個人を選ぶ項目がありません。
- Yahoo!ニュースとタイムラインは新規受付停止中（フォームに記載）。
- フィード仕様は非公開（契約後に案内）。必要になったら https://ai-tech-express.jp/feeds/news.xml を元に作ります。

---

## 8. 運営者のチェックリスト

今日:
- [ ] NewsPicks のフォームを送る（§3）
- [ ] Search Console でニュースサイトマップを送信済みか確認（§4）
- [ ] グノシーのフィードを検証ツールにかけ、エラーがないか確認してから申込む（§5）
- [ ] 申込みフォーム用に本名・住所・電話番号を用意する（サイトには載せなくてよい項目もある）

サイトに足すと審査で有利になるもの（どれも運営者本人の情報なので、こちらでは書いていません）:
- [ ] 運営責任者を本名で書く。いまは「koyuiw7」というハンドル名（site.config.ts の `operator.representative`）
- [ ] 所在地を少なくとも都道府県まで書く（いまは「日本」だけ）
- [ ] 編集責任者の経歴を一文（運営者情報ページ）
- [ ] お問い合わせを Web フォームでも受け付ける（Google フォームで可。NewsPicks が推奨）
- [ ] 公開前に人が目を通しているなら、編集方針にそう書く。通していないなら書かない（事実でないことを書くと逆効果）
- [ ] 月間PV を Cloudflare Web Analytics で確認して記録する

中長期（スマートニュース・LINE NEWS を狙うなら）:
- [ ] 法人化（合同会社で可）し、法人名と代表者名をサイトに書く
- [ ] プレスリリースが元の記事の比率を下げ、比較・検証・背景解説など独自の記事を増やす
- [ ] 「1日100本」に増やす計画は、Google の「大量生成コンテンツの不正利用」とニュースアプリの審査基準の両方に引っかかるおそれがあるので、配信用フィードには引き続き分量のある記事だけを載せる
