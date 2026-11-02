# サイソクニュース 開発ガイド（AIアシスタント向け）

このファイルは、ChatGPT / Codex / Claude などのAIが、このリポジトリで開発や補助をするときに最初に読む説明書です。
オーナーはエンジニアではありません。**返事は日本語で、専門用語を避けて短く**伝えてください。

---

## 0. 読者（ペルソナ）— すべての記事・SNS投稿はこの人のために作る（2026-11-02 オーナー決定）

- **誰に**: 20〜50代の働く男性（会社員・個人事業主）。今の仕事で成果を出したいだけでなく、副収入・独立・起業・資産づくりも考えている。仕事とお金への意欲が高い。
- **困りごと**: ①忙しくてニュースを追う時間がない ②ニュースは多いのに「自分にどう関係するか」「何をすればいいか」が分からない ③気づいたときには、もう周りが動いたあと。
- **約束**: 最速／取りこぼさない／3行でわかる／動ける（「仕事・お金にどう活かせるか」「明日から何ができるか」まで書く）。目指すのは「サイソクで知れたから、すぐ動けた」。
- **話題の優先**: AI・テック／ビジネス・お金・投資／不動産／起業・副業／税金・補助金など暮らしの制度／スポーツ（集客の入口。大きな結果やニュースだけ）。
- **スポーツの範囲**: 日本のスポーツだけ（日本のリーグ・チームと、海外で活躍する日本人選手）。海外の選手・リーグそのものは扱わない。例外としてオリンピック・パラリンピック・世界陸上・FIFAワールドカップ・WBC・ラグビーW杯・世界水泳などの世界的な大会は海外選手も扱う。スポーツ以外のジャンルは世界中が対象。
- **話し方**: 頼れる先輩のように、落ち着いて具体的。数字と根拠を出す。あおらない。
- **やらないこと**: 根拠のない儲け話、あおる見出し、特定の銘柄・物件を「買うべき」とすすめる個別の投資助言（金融商品取引法の投資助言に当たらないよう、一般的な情報と判断材料の提示に留める）。
- **SNSプロフィール（決定済み）**: 気づけば、周りはもう動いてる／そうならないための最速ニュース⚡／忙しくても、サイソクで「旬」がわかる／何ができるか、どう使うか、何に役立つかが一瞬で。
- **仕組みでの置き場所**: プロンプト・判定は `scripts/persona.py` の1か所だけ（記事・SNS・ジャンル配分・スポーツの範囲）。文面を他のファイルに複製しない。詳しくは `docs/persona-20261102.md`。

## 1. サイソクとは

- **サイト**: https://ai-tech-express.jp （**サイソクニュース**。2026-11-02 に「サイソクAIニュース」から改名。SNS 4つも同じ名前。旧名は構造化データの alternateName に残している）
- **タグライン**: 仕事とお金の“旬”を、どこよりも速く。
- **一言で**: AI・ビジネス・お金・投資・不動産の新しい動きを、**公式発表から最短数分で**日本語の記事にするニュースサイト。
- **強み**: 「どこよりも最速」。速報が最優先。
- **記事の特徴**: 結論→要点（3行）→「日本で使える？」「無料？」→「仕事・お金にどう活かせる？」まで書く。専門用語なしの「かんたん版」と「専門家版」を切り替えられる。
- **運営**: 個人（オーナー1人）。記事・速報・SNS投稿はほぼ全自動。

## 2. 目標（数字）

| 時期 | 直近30日の目標粗利 | 日本の訪問者（1日） |
|---|---|---|
| 2026/11/26 | 10万円（努力目標） | 2,500人 |
| 2027/01/26 | **50万円（必達）** | 10,000人 |
| 2027/04/26 | 100万円 | — |

2026/10/30のオーナーの最新訂正により、この計画での「目標粗利」は次の式で追う。以前の全経費控除後の利益という定義を置き換える。
**判定日を含む直近30日の承認済み報酬などの確定売上 − 同期間に対応する、サイソク拡大のため今後新たに発生する費用。**
対象は広告費、新規契約、既存契約の増額・従量利用の増加、外注・検証費など、サイソク拡大に伴う追加分。契約後の継続費も対象とし、初回だけ差し引いて済ませない。
既存の共用ツール費・既存の運営固定費・他事業の費用は、この目標粗利から控除せず参考管理とする。既存共用費の円決済額や按分の調査を、集客改善の前提条件にしない。
未承認の発生額・入金額・30日換算ペースは別に記録し、達成額と混同しない。控除対象の追加費用が未確認なら0円扱いしない。追加投資の評価では「投資によって増えた承認報酬 − 追加費用」も別に確認する。

日々の数字は `.claude/metrics.json`（ローカルのみ）に記録している。

## 3. 仕組みの全体像

```
公式発表・ニュース ──▶ 見張り番(Cloudflare Worker, 2分ごと) ──▶ GitHub Actions で記事生成(Python + LLM)
                                                               │
                                     品質チェック(読みやすさ・事実確認) ─┤
                                                               ▼
                         content/articles/*.json ──▶ Next.js をビルド ──▶ Cloudflare Workers で公開
                                                               │
                                       SNS自動投稿(Threads / X / Facebook / Bluesky) ・ プッシュ通知 ・ メルマガ
```

- **サイト本体**: Next.js 16（App Router）＋ OpenNext で **Cloudflare Workers**（worker名 `saisoku-site`）に載せている。Vercel はもう使っていない。
- **記事を作る側**: `scripts/` の Python。GitHub Actions（`auto_run.yml`）が定期実行する。
- **速報の見張り**: `ops/watchdog/`（別の Cloudflare Worker）。公式サイト65社以上を2〜30分ごとに見て、大きな発表があれば記事生成をすぐ起動する。地震・津波（気象庁）も見ている。
- **公開**: main に push すると `deploy_cloudflare.yml` が自動でデプロイ（約3〜5分）。

## 4. フォルダの地図

| 場所 | 中身 |
|---|---|
| `app/` | ページ（トップ、記事、/news、/guides、/choose、/products、/ai-pricing など） |
| `components/` | 画面の部品 |
| `lib/` | 共通の処理（記事の読み込み、広告、インデックス判定など） |
| `cloudflare/` | Worker 側の処理（プッシュ通知、クリック計測、画像配信など） |
| `content/articles/` | 記事データ（1記事＝1つのJSON）。**ボットが毎日大量に更新する** |
| `content/howto/` | 「使い方・料金」の解説記事データ |
| `content/offers.json`, `content/affiliate_links.json` | 広告（A8・楽天・Amazon）の設定。`scripts/a8_import.py` で作り直す |
| `scripts/` | 記事生成・品質チェック・画像・SNS投稿・計測などのPython |
| `ops/watchdog/` | 速報の見張り番 Worker |
| `state/` | 自動処理の記録（投稿履歴など）。ボットが更新する |
| `docs/` | 文字の見やすさのルール（`typography.md`）、自動テストの決まり（`automation.md`）など |
| `.github/workflows/` | 自動実行の設定 |

## 5. 主な自動処理（GitHub Actions）

| ワークフロー | 役割 |
|---|---|
| `auto_run.yml` | 記事の生成（通常＋速報）、画像付け、公開判定 |
| `deploy_cloudflare.yml` | サイトの公開 |
| `breaking_post.yml` | 速報のSNS即時投稿＋プッシュ通知 |
| `sns_extra.yml` | 朝7:30のまとめ投稿、火・金の問いかけ投稿 |
| `bluesky.yml` | Bluesky への投稿（Threads の写し＋直近の良い記事。1日24本まで・40分以上あける。記事の自動処理の後にも動く） |
| `evergreen.yml` | 「使い方・料金」解説記事を1日4本 |
| `missed_check.yml` | 毎朝8:30、取りこぼした大ニュースがないか確認 |
| `newsletter.yml` | 毎朝7時のメルマガ |
| `readability.yml` | 文字の見やすさの自動チェック |
| `pricing_watch.yml` | 毎朝6:17、公式の料金ページを読み直して料金の段の表（`content/pricing/*.json`、円の目安つき）を更新。金額が変わったら記事の候補にする。読み方は `scripts/pricing.py`（Python）と `lib/pricing-ladder.ts`・`components/PriceTable.tsx`（サイト） |

## 6. 大事なルール（必ず守る）

### 中身
- **事実だけを書く**。推測で数字や機能を作らない。出典は公式発表を優先し、確認日を書く。
- **地震・津波の記事はAIに文章を書かせない**（気象庁のデータを型に入れるだけ）。推測・被害の記述は禁止。
- 誇張した見出し・「戻れなくなる」等の根拠のない言い切りは禁止。

### 見た目（オーナーの好み）
- デザインは「報道ネイビー×速報レッド」（紺 `#1d4a8f` / 赤 `#c8221a` / 背景 `#f3f5f8` / 文字 `#0b1a33`）。
- **嫌いなもの**: 地味すぎる見た目、ごちゃごちゃ、AIっぽいボタン（右上矢印「↗」など）、黄色いマーカー、Zen Kaku フォント、PR感の強い広告枠。
- 広告は**ふつうのページと同じ見た目で自然に**。広告表記は小さな1行（法律上必要なので消さない）。
- 文字の見やすさ: `npm run check:readability` が **0件** であること（`docs/typography.md`）。

### 広告・計測
- 広告リンクは `rel="sponsored nofollow noopener noreferrer"`。広告主の素材URL・計測タグを独自に作り替えない。2026/10/30のオーナー指示による例外は、Dify比較の指定XServer VPSリンク2か所への**A8公式パラメータ機能のid1〜id3追記だけ**。元のa8mat・遷移先・エンコード・画像タグは保持し、他の広告へ広げない。仕様・制約は `docs/dify-a8-attribution-20261030.md` を参照。
- テストでブラウザを動かすときは、a8.net・楽天・Amazon・AdSense への通信を止める（`docs/automation.md`）。止めないと広告の数字が水増しされる。
- 訪問者数は **Cloudflare Web Analytics（ロボット除外）** を見る。Facebook からの大量アクセスはロボット（リンクプレビュー）なので人として数えない。
- 広告の「人によるクリック」は `python3 scripts/aff_stats.py --yesterday`。

### 安全
- **秘密の値（APIキー・パスワード・トークン）を画面やログに出さない**。保存場所は GitHub Secrets / Cloudflare の secret。
- パスワード入力・課金・規約への同意・アカウント作成は**オーナー本人が行う**。
- オーナーの名前でSNSに投稿・コメントするときは、**必ず事前にオーナーのOKをもらう**。
- 別プロジェクト（マイソク: maisoku-maker.jp、ポート3000 など）には触らない。
- 住所・電話番号はサイトに載せない（サイト上は「東京都」まで）。

## 7. 作業の進め方

1. `git fetch && git merge origin/main` で最新にする（ボットが頻繁に push している）。
   - 衝突したら `content/*`・`state/*`・`content/llm_usage.json` は **main 側（相手）を採用**。
2. 変更する。
3. 確認: `npx tsc --noEmit` / `npm run build` / `npm run check:readability`（0件）。
4. コミット → main に push → `deploy_cloudflare.yml` の成功を確認 → 本番で表示を確認。
5. オーナーへの報告は、**結果を最初に、日本語で短く**。

## 8. 主な秘密・設定の名前（値はここに書かない）

- GitHub Secrets: `ANTHROPIC_API_KEY`（Claude API のクレジット。速報・SNS向け記事・動画の台本だけ）, `CF_DEPLOY_TOKEN`, `BUFFER_API_KEY`, `RESEND_API_KEY`, `JINA_API_KEY`, `BLUESKY_APP_PASSWORD`, `VAPID_PRIVATE_KEY`, `PUSH_SEND_TOKEN`, `AFF_STATS_TOKEN` ほか
- GitHub Variables: `WEB_DAILY_CAP`（通常記事の1日上限）, `THREADS_MODE`, `BLUESKY_HANDLE`, `EVERGREEN_DAILY_CAP`, `SNS_FORMAT`, `SNS_DIGEST`
- Cloudflare: アカウント `51e37d82e15d397b9666dc3d09bd5b92`、KV（記事キャッシュ・画像・メルマガ・プッシュ・クリック計測）

## 9. アカウント（参考）

- SNS: Threads `@saisoku637` / X `@saisoku_ai` / Facebookページ「サイソクニュース」/ Bluesky `@ai-tech-express.jp`
- 収益: A8.net（通信・転職・ガジェットなど）、楽天（A8経由）、Amazonアソシエイト（`saisokuai-22`、180日以内に3件の販売が必要）、AdSense（審査中）
- 検索: Google Search Console（`sc-domain:ai-tech-express.jp`）、Bing Webmaster Tools、IndexNow

---
最終更新: 2026-11-02

最近の変更: [通常SNS投稿の403修正・掲載検証の記録](docs/sns-403-fix-2026-10-30.md)。復旧確認で前倒しした1件は、履歴を保持しつつ形式比較の学習対象から除外している。

- 計測とDify導線の変更・限界・運用判断: `docs/revenue-funnel-2026-10-30.md`。短期収益より長期の信頼を優先し、無料で足りる読者に契約を勧めない。

- Dify検索入口2記事・変更監視・検索申請・検証と未確認事項: `docs/dify-acquisition-2026-10-30.md`。今回は検索施策のみ。Threads草案に投稿許可はない。

- 定時SNSの遅延回収・媒体別履歴/Buffer照合・商品更新の分離: `docs/automation-recovery-2026-10-30.md`。今回の朝まとめ/火金企画の復旧検証はオーナー依頼済み。Dify単発草案の投稿許可とは別。商品更新は`product_refresh.yml`で行い、記事処理へ戻さない。SNSの受理不明記録を消して再送しない。

- 検索流入改善（ChatGPT/IPA/Work IQ、Dify振分け、sitemap、手動改稿保護）: `docs/search-growth-2026-10-30.md`。`refreshMode: editorial` は自動再生成せず編集待ちを記録する。確認後はcheckedAtを更新しneedsReviewとneedsReviewReasonを消す。

- 検索クリック改善（Copilot Fusion/IPA/Work IQの題名・回答・利用条件）: `docs/search-ctr-2026-10-30.md`。公開・Google再取得・検索表示・クリック増を別段階で判定し、少数の表示や未反映の数字で成否を断定しない。

- Google再取得申請エラーの切り分けとトップDify入口: `docs/google-reindex-recovery-20261030.md`。本人のChrome更新後も個別申請は送信/reCAPTCHA接続エラー。公開URLテスト成功、Googleサイトマップは通知受付と当日読み込み成功を確認済み。個別申請修復・記事再取得・検索反映を完了扱いしない。

- Dify費用記事のクレジット計算・Cloud制限FAQ・公式監視2→4資料: `docs/dify-search-utility-20261030.md`。計算は読者が確認した消費量からの目安で請求額や成功回数の保証ではない。監視は既存基準を保持し編集候補のみ作る。公開・検索登録・検索流入・収益を区別する。

- 検索クリック改善の追加3記事（PHP/KDDI/釣八）と記事一覧: `docs/search-ctr-next-20261030.md`。公式資料に沿って対象・条件・時期を明確化し、URL/広告/SNS履歴を保持。通常ニュースにはガイドと同じ手動編集保護はなく、自動続報後は冒頭や説明の整合を確認する。非公開の検索数値は公開repoへ入れない。

- ブランド検索・媒体ロゴ・可視署名・速度根拠: `docs/search-identity-20261031.md` と `docs/latency-evidence-20261031.md`。favicon配信は正常、旧Vercel404転送は未解消。旧speedMinutesは推定のため公開実測として再表示しない。検索設定の監査CLIと、欠測/精度を分けた速度集計を使う。新サイト公開とGoogle側のアイコン・順位・AI概要の反映を混同しない。1日9投稿枠・既存広告・他事業の設定を保持する。

- Dify公式変更→回答更新の経路: `docs/dify-answer-update-20261031.md`。5公式資料を1日4回監視し、同じrunのpacket/stateでレビューする。初期動作は提案のみ、内容確認後の明示applyだけ既存2ガイドを更新。手編集・原文hash・重複・広告を保護し、部分確認で全体checkedAtを進めない。検索需要は実測まで仮説。
- SNSとサイトfaviconの統一: `docs/favicon-sns-20261031.md`。既存赤白の原本を使い、ICO6サイズと安定PNGを指定。旧icon/apple/manifest/通知経路を保持。サイト配信の成功とGoogle検索アイコンの反映を区別する。
- GeminiのExcelクローン記事の信頼性・検索入口の改善と未検証のM5比較記事: `docs/search-gemini-excel-20261031.md`。第三者デモと公式発表を分け、Googleの反映・検索流入は別途確認する。

- M5 UltraとRTX 5090の比較記事の仕様・測定条件・価格の訂正: `docs/mac-studio-correction-20261031.md`。256GB検証機と最大512GB仕様を区別し、形式や試行の異なる数値を混ぜない。通常記事の自動再生成・古いSNS文面に注意。

- Buffer受付後の配信状態・予約画像の読み取り専用監視: `docs/buffer-delivery-monitor-20261031.md`。Buffer受付を配信成功と混同しない。既存の重複防止履歴や予約を変更せず、不明時は自動再送しない。

- ホームのストーリーから記事へ移動した後のスクロール修正: `docs/story-scroll-fix-20261031.md`。ストーリー終了・画面遷移時はbodyの元のoverflowを復元し、広告通信を遮断してPC/スマホで検証する。

- 読者体験・Meta・PR価格の修正: `docs/reader-experience-meta-20261031.md`。通常版とFlow Editionの価格を混同しない。ストーリー件数は表示記事数であり発表総数ではない。全製品の事実確認や流入効果は未完。

- PR商品画像・条件・販売期限: `docs/pr-product-quality-20261031.md`。OZ Z1は売切れ確認のため非表示、手動で再確認する。View 380は期限つきで、時刻は端末依存。広告素材URL・成果計測URLは変更しない。

- PRの関連性・料金条件・未確認範囲: `docs/pr-catalog-trust-20261031.md`。記事の主題で選び、広告枠のために無関係な書籍を補充しない。価格・在庫は商品ごとに確認し、全件確認済みと混同しない。

- Museの日本提供訂正・出典照合と価格鮮度: `docs/growth-trust-muse-20261031.md`。Meta日本一覧と4商品を読取専用で監査するが、未監視47件を確認済みと扱わない。カタログ価格は確認時刻から48時間で金額を隠す。旧投稿・予約は変更していない。

- Web速報の文字数条件の撤去・有用性確認・動画試作の境界: `docs/short-news-and-video-20261101.md`。動画原典の記事の生成文字数指示と、まとめ向け2000字配信選定は別途残存。SNS 動画の試作は未投稿。外部素材・音声の権利を確認するまで自動投稿しない。

- 2026/11/1の検索入口・訪問数の基準とAPI特典記事の訂正: `docs/daily-growth-and-api-correction-20261101.md`。API特典は申請・対象・期限・別請求を維持し、旧SNS文面の再利用に注意。検索・訪問の計測は集計範囲を分ける。

- 記事の写真の速さと著作権（2026/11/2）: 元記事の写真は**公式の発表（会社・団体の自社サイト、PR TIMES などのプレスリリース配信）だけ**使い、幅1200pxまで縮小してWebP（400/640/800/1200）と一緒に自前のKV（saisoku-images）から配信する。写真の下に「画像：◯◯（公式発表より）」を出す。ニュースサイト・ブログ・SNSの写真は保存も直リンクもせず、フリー素材・AI生成にする。仕組みは `scripts/images.py` の冒頭（`--cache-official`・`--variants`）と `lib/thumb-url.ts`。YouTube のサムネイルは従来どおり YouTube から読む。
- 検索に出す記事の基準は1つ（Python `scripts/indexnow.py` の `noindex_reason`／サイト `lib/indexing.ts` の `noindexReason`。サイトマップ・ニュース用サイトマップ・記事の robots・IndexNow が同じ判定を使う）。主題（off-focus）は読者像に合わせる: AI・テック／`persona.is_persona_topic`（ビジネス・投資・不動産、制度・暮らしのお金の話題）／日本のスポーツ（`persona.sports_scope`）。判定の値は `lib/index-rules.json`（`python3 scripts/indexnow.py --export-rules` で生成。ずれは `tests/test_index_rules.py` が検出）。本文1500字未満（thin）は下げない。
- 重複記事の統合（duplicateOf・noindex・canonical）、古い発表の作り直し停止とニュース用サイトマップの48時間ルール、料金・リリースノートの見張り、他社との突き合わせ: `docs/parity-fixes-20261102.md`。重複の判定は `scripts/story.py` だけで行い、サイトは JSON の duplicateOf を読む。
- 改名（サイソクニュース）と読者像（ペルソナ）、記事の「仕事・お金にどう活かせる？」欄、SNSの活用型・お金インパクト型、幅広い話題の優先順位・スポーツの範囲と1日上限: `docs/persona-20261102.md`。読者像の文面は `scripts/persona.py` だけで直す。
- SNS 動画 v3「画面で見せる型」（試作・未投稿・スケジュール無し）: `docs/sns-video-v3-20261102.md`。公式ページを本物のブラウザで撮って数字へズーム、文字はキーワードだけ、締めのカード無し。`scripts/shorts_v3.py`・追加ゲート `video_gate.check_v3`・手動の `shorts_v3.yml`。CI で約2〜3分。
- SNS 動画 v2（試作・未投稿・スケジュール無し）: `docs/sns-video-v2-20261102.md`。3デザイン（報道テロップ/大きな文字/公式ページ）、品質ゲート `scripts/video_gate.py`、失敗時は動画なしで今までどおり投稿する約束 `scripts/sns_video.py`（既定は無効）。投稿用の声は VOICEVOX のみ。

- 朝のまとめ「今朝のAIニュース3本」の号ページ `/digest/<YYYYMMDD>`（noindex）とシェア画像 `share.png`（1200×630）・`card.png`（1080×1350）。デザインは `lib/digest-design.ts` の `DIGEST_DESIGN`（A/B/C）1か所で切替、`?design=` で見比べ。号のデータは `scripts/digest_page.py` が `content/digests` と KV（デプロイ前の朝用）に置く。見出しは会社名だけにしない（`short_headline`）。要約の見出しは「要約すると」。

- Claude の使いどころ（2026-11-03 オーナー決定）: Claude（API のクレジットも CLI のサブスクリプションも）は**速報・SNS に出しそうな記事・動画の台本だけ**。順番は Claude API → Claude CLI → 無料モデル。ほかの記事・品質チェック・画像などは無料モデルだけ。呼び出しは `llm.generate_json(..., visibility="high", purpose="breaking"|"sns"|"video"|"video_review")`。API は月 $95（補充日 毎月4日で区切る）・1日の目安・purpose ごとの回数上限を `state/claude_api_usage*.json` で守り、クレジット切れは次の補充日まで自動で使わない。仕組みは `scripts/llm.py` の冒頭。

- 記事の「すぐできること」（2026-11-03 の比較調査 `out/記事の比較調査_2026-11-03.md` の6章）: 日本での提供の表 `content/japan_availability.json`（人が確かめて更新。出典と確認日つき。確かめられないものは unknown）を `scripts/japan_availability.py` が生成・事実確認・判定に使う（一般提供中の製品だけ unknown を直す）。料金・使い方ガイドへの固定リンクは `lib/guide-links.ts`。判定カードはジャンル別（`lib/verdict.ts` の `verdictKind`）。FAQ・今日やることは合うジャンルだけ。サイソクが自分で試したように読める書き方（「筆者」など）と、題名の約束に本文が答えていない記事は品質ゲートで落とす。公開済みの記事の機械的な手直しは `python3 scripts/article_quickfix.py [--apply]`。

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
