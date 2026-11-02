# 大手ニュースサイトとの差を埋める修正（2026-11-02）

競合調査（重複記事・古い発表の作り直し・取りこぼし）を受けた修正。GPT / Codex / Claude が続きを触るときの決まりごと。

## 1. 同じ発表の重複記事

- 判定は `scripts/story.py` の1か所だけ。結果を記事 JSON の `duplicateOf`（代表の slug）と `duplicateReason` に書き、
  サイト（`lib/indexing.ts`・`lib/article-index.mjs`）と IndexNow（`scripts/indexnow.py`）はそれを読むだけ。
- 同じ話題とみなす条件（載せた時刻か元の発表の時刻が5日以内）: 出典 URL が同じ（クエリ・utm・末尾の /・www・言語の段を除く。
  openai.com/blog→/index。YouTube は動画 ID）／題名が同じ／元の題名（動画・元記事）が同じ／同じ会社で題名に同じ製品名
  （「名前＋版数＋名前」をひとまとめ。GPT-6 Astra と GPT-6 Sol は別）／同じ会社で日本語の題名に英字のまま同じ珍しい固有名詞
  （Legora など。題名かキーワードも似ていること）／同じ発信元が6時間以内に出した同じ珍しい固有名詞（1つの事例を何本にも分けた動画）。
  会社が両方わかって重ならない・ジャンルが違うときは別の話題。
- 重複にしないもの: `angleOf` の付いた記事（`scripts/angle_articles.py` が付ける。手で付けてもよい）と、先の記事へリンクした
  解説の形の題名（使い方・比較・誰が使える 等）。例: GPT-6.1 Terra の3本、Gemini 4 Argon の Excel デモ記事（`angleOf` を手で付けた）。
- 代表: ほかの理由で検索から外れない記事のうち最初に載せたもの。手動の `indexable: true` は代表・印なし。まとめ記事・地震は対象外。
- 重複記事: `noindex, follow`、canonical は代表の記事、サイトマップ・ニュース用サイトマップ・IndexNow から外す。ファイルは消さない。
- 印は付けたら外さない（`python3 scripts/story.py mark --recompute` のときだけ付け直す）。
- 生成前にも同じ判定で外す: 動画（`scripts/main.py`）は見送り、公式フィード（`scripts/web_articles.py`）は大きなニュースなら
  続報の判定（`updates`）へ、それ以外は見送り。`auto_run.yml` の「Mark duplicate stories」が別の回・別レーンで作られた重複を拾う。
- 2026-11-02 の実施結果: 47本に印（Legora・Genie 4・Gemini 4 Carbon・Zendesk・Mistral Large 4・Gemini Robotics 2・GPT-6 Astra の
  初見動画など）。「同じ分に3本」の Legora は、OpenAI が YouTube に同じ事例の動画を3本同時に出し、動画の経路に重複判定が無かったのが原因
  （並行実行の競合ではない。auto_run は concurrency で直列）。

## 2. 古い発表と日付

- 元の発表から48時間を超えたものは通常の記事にしない。公式フィードは記事ページの公開日時（`article:published_time`・
  `og:published_time`・JSON-LD `datePublished`）でフィードの日付・「初めて見た時刻」を直してから判定（`cap_with_page_date`）。
  大きなニュースで記事がまだ無いものだけ `late: true` で「○月○日に発表された」と書かせて作り、速報投稿しない。
- AI・テックの YouTube チャンネルは投稿から48時間まで。投資・不動産などの解説チャンネルは従来どおり10日までだが、48時間を超えたら `late`。
- レーダー（まとめサイトの見出し）から来た話題は、見出しの時刻を発表の時刻として使わない。公式ページの日付で12時間以内と分かったときだけ速報。
- ニュース用サイトマップ（`lib/indexing.ts` の `newsSitemapReason`。Python は `indexnow.news_reason`）: 検索対象・重複でない・
  解説（angleOf）でない・まとめでない・`late` でない・載せてから2日以内・元の発表（`sourcePublishedAt`、無ければ `publishedAt`）から
  載せるまで48時間以内。公開日は `generatedAt`（書き直しで変えない）。2026-11-02 時点で24本中14本が「古い発表の作り直し」で外れた。

## 3. 取りこぼし対策（一覧は `scripts/sources_registry.json` に集約）

- `watch_pages`（`scripts/page_watch.py`・auto_run の通常の回＝約20分ごと）: Claude アプリ/API のリリースノート・モデル提供終了予定・
  claude.com/pricing、ChatGPT リリースノート/料金（r.jina.ai 経由）、Gemini リリースノート/プラン、Microsoft 365 Copilot
  リリースノート、GitHub Copilot のプラン。dated は新しい日付の項目、diff は金額・プラン名・上限の行が2回続けて変わったとき。
  初回は基準だけ。候補は大きなニュースと同じ流れ（重複・続報判定・品質ゲート）で作り、速報投稿はしない。状態は `state/page_watch.json`。
- `radar_sources`: レーダーが見る追加の報道・まとめ（GIGAZINE・AI Watch・Impress Watch・Yahoo!トピックス IT・CNET Japan・
  TechCrunch AI・The Verge AI・9to5Google）。見つけるためだけで、公式扱い・出典にはしない（`every` 分ごとに読む）。
- レーダーの語に、改名・統合・上限の引き上げ・値上げ・新プランを追加。会社に LINEヤフー・SB Intuitions・NTT・KDDI・楽天・富士通・NEC・
  PFN・Sakana AI・ELYZA を追加（決算・訴訟・人事は従来どおりレーダーの自動起動の対象外）。
- 発信元（速報レーン）: claude.com/resources/articles（tier A）、SB Intuitions、NTTデータ、ストックマークを追加。PFN・Honda の一覧の読み方を直して再開。
  LINEヤフーは robots.txt が 403 のため止めたまま（レーダーで拾う）。
- 通常記事（幅広い話題）: 財務省（入札結果を除く）・国交省 報道発表・J-Net21・住友不動産・野村不動産HD を追加。
- `detect_feeds`: 毎朝の見逃しチェック（`radar.py missed`）だけが使う他社の見出し（経済・国内・不動産・スポーツなど）。
  他社だけが出した話題を Issue に並べる（Yahoo!トピックスなど編集部が選んだものは1社でも、それ以外は2社以上）。依頼には自動で回さない。

## まだのこと

- X（旧 Twitter）の公式アカウントは読む経路が無いので見ていない。
- NEC（ボットの UA だと 403）、富士通（JS チャレンジ）、PR TIMES の企業別フィード（配信の扱い）、経産省・中小企業庁（WAF）、
  三井不動産・東急不動産・NPB・B.LEAGUE（一覧ページ。速報レーンに不動産・スポーツの判定が無い）は未対応。
- 米大手の決算（IR ページ）・訴訟の一次情報の自動化は未対応（見逃しチェックの一覧には出る）。
- 不動産・投資は YouTube 専用のジャンルで、公式フィードの記事は business として作られる。
