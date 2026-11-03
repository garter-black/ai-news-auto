# オーナーのルールの台帳（直されたことを、仕組みで二度と繰り返さない）

オーナーに直されたことは、メモや「分かりました」で終わらせず、**コードで守らせる**（門番・テスト・毎日の見張り）。
この台帳は、オーナーが直したこと・決めたことを1行ずつ並べ、それを何が守らせているかを書く。

- **決まり（AGENTS.md）**: オーナーが何かを直したら、同じ変更の中でこの台帳に1行足し、守らせるテスト・門番・見張りを付ける。
- **台帳そのものの検査**: `tests/test_owner_rules_ledger.py`（`owner_rules.yml` で push のたびに動く）
  - 「強制」の欄に書いたファイルとテストが実在するか
  - 書いたテストが GitHub Actions のどこかで本当に動いているか（動いていないテストは強制とみなさない）
  - 毎日の見張り（`scripts/owner_rules_audit.py`）が確かめる番号と、この台帳の「点検」の印がずれていないか
- **毎日の見張り**: `.github/workflows/owner_rules_audit.yml`（毎日 4:40 日本時間・読むだけ）が本番の新しい記事・固定ページ・Bluesky の直近の投稿を点検し、
  違反を issue（ラベル `owner-rule-violation`）にまとめる。違反の行は下の番号（[A1] など）で始まる。無くなれば issue は自動で閉じる。
- 「点検」の欄の ● は毎日の見張りでも確かめているもの。
- 「会話」と書いたものは、オーナーとのやりとりの決まりでコードでは守らせられない。`~/.claude/CLAUDE.md` とメモリに置く。

## SNS 投稿

| 番号 | ルール | 日付 | なぜ | 強制 | 点検 |
|---|---|---|---|---|---|
| S1 | 1行目は関係する読者を名指しする呼びかけ（「〇〇な人、これは要チェックです」など。言い回しは直前と変える） | 2026-11-03 | 呼びかけの投稿だけ訪問が多かった | `scripts/sns_rules.py`（ルール6）・`tests/test_sns_rules.py::test_first_line_must_call_out_reader`・`tests/test_sns_owner_rules_20261103.py::test_callout_varies_and_passes_gate` | ● |
| S2 | 【速報】は本当の速報だけ（`breaking` か公式発表から60分以内） | 2026-11-03 | 何でも速報だと信用を失う | `scripts/sns_rules.py`（`is_fast_news`）・`tests/test_sns_rules.py::test_breaking_label_only_for_fast_news`・`tests/test_sns_owner_rules_20261103.py::test_breaking_prefix_follows_cover_rule` | ● |
| S3 | 「先に言うと、〜できない／対象外」「発表からN分で掲載」の書き出しは使わない | 2026-11-03 | データでクリックが0だった | `scripts/sns_rules.py`（`BANNED_OPENERS`）・`tests/test_sns_rules.py::test_banned_openers` | ● |
| S4 | 「要約すると、」＋短い事実2文。やさしい「です・ます」で主語を書く | 2026-11-03 | 記事の硬い文のままでは読まれない | `scripts/sns_rules.py`（ルール1・7）・`tests/test_sns_rules.py::test_structure`・`tests/test_sns_owner_rules_20261103.py::test_gate_rejects_stiff_summary` | ● |
| S5 | 問いかけは記事を読まなくても答えられる読者自身の好み・経験 | 2026-11-03 | コメントが反応を広げる | `scripts/sns_rules.py`（`ARTICLE_BOUND_QUESTION`）・`tests/test_sns_rules.py::test_question_answerable_without_article` | ● |
| S6 | 記事のリンクは本文に置く（X だけは返信に）。SNS ごとの utm を付ける | 2026-11-02 | リンクが無いと記事に来ない | `scripts/sns_rules.py`（`check_link`）・`tests/test_sns_rules.py::test_link_placement_and_utm` | ● |
| S7 | 画像は 16:9 の写真だけのカード（白い帯なし・主役を隠す文字なし） | 2026-11-03 | 写真がもったいない・主役が隠れる | `scripts/sns_rules.py`（`check_media`・`is_text_band`）・`tests/test_sns_rules.py::test_media_aspect_and_text_band` | ● |
| S8 | 1つの記事につき投稿は1つ。動画は記事の投稿に添える（動画だけの別投稿はしない） | 2026-11-03 | 同じ話の連投は嫌われる | `scripts/sns_rules.py`（ルール5）・`tests/test_sns_rules.py::test_one_post_per_article`・`tests/test_sns_video_followup.py` | ● |
| S9 | 決まった型・アンケートのふり・記事のない投稿は出さない | 2026-11-03 | 古い「AIの問いかけ」投稿が AI っぽいと言われた | `scripts/sns_rules.py`（ルール3）・`tests/test_sns_rules.py::test_poll_without_article_is_blocked` | ● |
| S10 | 「ざっくり」は使わない（「要約すると」） | 2026-11-02 | 使い古された言い方 | `scripts/sns_rules.py`（`BANNED`）・`tests/test_sns_rules.py::test_banned_and_generic_phrases` | ● |
| S11 | 自然な日本語（英語の文を残さない）・長さを守る・スポーツは日本のスポーツだけ | 2026-11-02 | 読者は日本の会社員 | `scripts/sns_rules.py`（ルール4）・`tests/test_sns_rules.py::test_english_sentence`・`tests/test_sns_rules.py::test_sports_scope` | ● |
| S12 | 投稿する経路はすべて門番を通す。ルールを変えたら全部の経路に当てる。古い自動投稿を残さない | 2026-11-03 | 古い問いかけ投稿の経路だけ直し忘れた | `tests/test_sns_rules.py::test_every_posting_api_call_goes_through_the_gate`・`tests/test_sns_rules.py::test_buffer_graphql_refuses_ungated_posting_mutation` | |

## 画像

| 番号 | ルール | 日付 | なぜ | 強制 | 点検 |
|---|---|---|---|---|---|
| I1 | 公式の写真が先（公式発表の写真 → 公式動画のコマ → AI 画像）。ニュースサイト・SNS の写真は使わない | 2026-11-02 | 品質と著作権 | `scripts/images.py`・`tests/test_images_official.py::test_media_and_unknown_are_not_official` | |
| I2 | 発表された製品・車・端末を AI で描かない | 2026-11-03 | AI の絵を本物の製品と受け取られた | `scripts/images.py`（`product_launch`）・`tests/test_images_official.py::test_ai_prompt_forbids_product`・`scripts/sns_rules.py`（`check_media`） | ● |
| I3 | 文字だけの図の画像を記事の写真にしない | 2026-11-03 | 写真の代わりにならない | `scripts/images.py`（`image_problem`・`graphic_problem`）・`tests/test_owner_rules_gaps.py::test_text_only_figure_is_rejected` | |
| I4 | AI の画像には「AI生成」と出す | 2026-11-03 | 本物と間違えられないように | `components/ImageCredit.tsx`・毎日の見張り `scripts/owner_rules_audit.py` | ● |
| I5 | 壊れた画像を出さない | 2026-11-02 | 壊れたアイコンは信用を落とす | `scripts/check_images.py`（`broken_images.yml` 1時間ごと）・`tests/test_check_images.py` | ● |
| I6 | 公式の写真・図を本文に1〜2枚、出典（画像：会社名・公式発表へのリンク）つきで置く。会社・団体の自社サイトの発表にある報道向けの画像だけ（ロゴ・通信社の写真・ニュースサイトの画像は使わない） | 2026-11-03 | 他社は公式の図や写真を出典つきで入れていた | `scripts/images.py`（`body_images`）・`lib/body-images.ts`・`tests/test_body_images.cjs`・`tests/test_images_official.py` | |

## 記事

| 番号 | ルール | 日付 | なぜ | 強制 | 点検 |
|---|---|---|---|---|---|
| A1 | 決まった型の小見出しを使わない（自分に関係ある？／ライバルと比べてどう？／もう使える？取引できる？／何がすごいの？／料金は？／日本で使える？） | 2026-11-03 | ロボットっぽい・恥ずかしい | 公開前の門 `scripts/article_style.py`（決まった型の見出し）・`tests/test_article_style.py::GateTest`・表示の段 `scripts/section_gate.py`・見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::RecordTest` | ● |
| A2 | 答えになっていない欄（元記事には書かれていません／判断できません／つなぎの編集部注）は欄ごと省く | 2026-11-03 | 読者の時間を無駄にする | `scripts/quality.py`（`faq_problems`・`promise_problems`）・表示と生成で文ごと消す `scripts/section_gate.py`（`SOURCE_NONANSWER_RE`）・`lib/article-sections.ts`・`tests/test_section_gate.py::test_no_data_cells_and_source_non_answers`・`tests/test_verdict_label.cjs`・見張り `scripts/owner_rules_audit.py` | ● |
| A3 | 中身のない文（〜にとっても重要です／〜といえます）を書かない | 2026-11-03 | 水増しに見える | 見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::RecordTest` | ● |
| A4 | 落ち着いた大人の文（ITmedia・日経クロステックの水準）。幼い言い回し（すごい／〜しちゃう／〜を作る道具）・ざっくりは使わない | 2026-11-03 | 「幼すぎる表現は本当にやめたい。恥ずかしい」 | `scripts/persona.py`（`STIFF_RE`）・見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::RecordTest` | ● |
| A5 | 題名は自然な日本語（有名な会社に「ChatGPTのOpenAI」のような説明を付けない・「」、を使わない・変な記号や区切りを使わない） | 2026-11-03 | 「日本語として変なタイトル」 | `scripts/headline_normalize.py`（区切り・かっこ・会社名。別の作業で拡張中）・`tests/test_headline_normalize.py`・`tests/test_headline_display.cjs`・見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::TitleTest` | ● |
| A6 | 札・見出しをあざとくしない（「3秒でわかる」など） | 2026-11-03 | 安っぽく見える | 見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::PageTest` | ● |
| A7 | 他社との比較は本物のデータがあるときだけ | 2026-11-03 | 作った比較は信用を失う | `scripts/quality.py`（`rival_problems`）・`scripts/rivals.py`・`tests/test_rivals.py::test_rejects_quote_not_on_page` | |
| A8 | 「使える」の札は本当の製品・サービスの記事だけ | 2026-11-03 | 情報流出・買収・統計に「使える」は変 | `lib/verdict.ts`・`tests/test_verdict_label.cjs`・`tests/test_verdict_scope.py::test_non_product_news_has_no_verdict` | |
| A9 | 画像の赤い「速報」は速報の記事だけ（ほかは「ニュース」） | 2026-11-03 | 何でも速報だと信用を失う | `lib/cover-path.ts`（`isFastNews`）・`tests/test_owner_rules_site.cjs` | |
| A10 | 価値の低い種類の記事を作らない（他人の動画の要約・プレスリリースの言い換え・話題外・薄い記事） | 2026-11-03 | AdSense に「有用性の低いコンテンツ」で落とされた | `scripts/retire_articles.py`・`scripts/indexnow.py`（`publish_block_reason`）・`tests/test_retired_sources.py::test_creator_channels_are_not_active`・`tests/test_publish_gate.py::test_thin_is_blocked_and_retryable` | ● |
| A11 | 広告はすべての記事に出す。ただし記事に関係あるものだけ | 2026-11-03 | 収益と信頼の両立 | `lib/offers.ts`・`tests/test_offer_relevance.cjs`・見張り `scripts/owner_rules_audit.py` | ● |
| A12 | 人の確認についての説明は実態どおり（「全記事を人が確認」と書かない。記事ごとの確認を約束しない） | 2026-11-03 | 事実と違う約束はしない | `tests/test_owner_rules_site.cjs`・見張り `scripts/owner_rules_audit.py` | ● |
| A13 | スポーツは日本のスポーツだけ（五輪・世界陸上・W杯・WBC などは例外） | 2026-11-02 | 読者像に合わせる | `scripts/persona.py`（`sports_scope`）・`tests/test_persona.py::test_foreign_athletes_and_leagues_are_out` | |
| A14 | サイソクが自分で試したように書かない（「筆者」など）。題名の約束に本文で答える | 2026-11-03 | 事実と違う書き方をしない | `scripts/quality.py`（`first_person_problems`・`promise_problems`）・`tests/test_article_quickwins.py::test_gate_rejects_first_person_testing` | |
| A15 | 地震・津波の記事は AI に書かせない（気象庁のデータを型に入れるだけ） | 2026-10-28 | 推測や誤りが許されない | `scripts/quake.py`・`tests/test_owner_rules_gaps.py::test_quake_never_uses_llm` | |
| A16 | 記事の数字は元記事・確かめた資料・計算済みの数字だけ | 2026-11-03 | 作った数字は信用を失う | `scripts/quality.py`（`number_problems`）・`tests/test_facts_bundle.py` | |
| A17 | 判定のひとこと・本文を「〜しましょう」「〜してみましょう」の呼びかけで締めない（「〜するとよいでしょう」など落ち着いた言い方。事実は変えない） | 2026-11-03 | 幼い・売り込みに見える | 生成の前に直す `scripts/section_gate.py`（`professional`・`apply`・`style_problems`）・表示 `lib/article-sections.ts`（`professionalSentence`・`cleanTake`）・`scripts/persona.py`（`PLAIN_STYLE`）・`tests/test_section_gate.py::test_professional_register`・`tests/test_article_sections.cjs`・見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::RecordTest` | ● |
| A18 | 記事はニュースの種類（決算・脆弱性・製品・料金・提携・書類・制度・調査・人事・イベント・速報・スポーツ・解説）ごとの型で書く。決まった章立てを使わない・材料の無い章を作らない・見出しは中身の言葉で本文1,000字に1つまで | 2026-11-03 | 「かなり大事」。決算にも脆弱性にも同じ章立てで、章の2割が「書かれていません」だった | `scripts/news_types.py`（判定と型）・`scripts/article_style.py`（見出しの数）・`tests/test_news_types.py::PromptTest`・`tests/test_article_style.py::GateTest`・見張り `scripts/owner_rules_audit.py` | ● |
| A19 | リードの1文目で「今回なにが新しいか」を言い切る。リード・要点・本文・まとめで同じ文や数字をくり返さない（「## 概要」の章は作らない） | 2026-11-03 | 他社は最初の1文で新しさを言い切る。サイソクは同じ事実を何度も書いていた | `scripts/article_style.py`（リード・くり返しの門と、本文の最初の重なりの自動の削除）・`tests/test_article_style.py::GateTest`・見張り `scripts/owner_rules_audit.py` | ● |
| A20 | 種類ごとに必ず入れる事実（決算の前年比・見通し、脆弱性の深刻度・対象の版・直し方・悪用の有無、製品の価格・発売日、制度の対象者・金額・期限など）は、資料にあれば入れる。元の資料は要約ページの先（財務諸表・修正版の告知・詳しい案内）まで読む。無い事実は書かない（推測で埋めない） | 2026-11-03 | 他社は読者が次に知りたい事実を先回りして書いていた | `scripts/news_types.py`（`missing_facts`。足りなければ作り直し）・`scripts/source_links.py`（種類ごとに深く読む）・`tests/test_news_types.py::MissingFactsTest`・`tests/test_news_types.py::DeepReadingTest` | |
| A21 | 文体の決まりは1か所（`scripts/article_style.py`）にまとめ、すべての生成の段（通常・速報・幅広い話題・話題のキーワード・動画・まとめ・別の切り口・かんたん版）で使い、公開前の門で守らせる（落ちたら作り直し、2回落ちたら出さない） | 2026-11-03 | 新しい決まりを足しても、古い指示が残っていて直らなかった | `scripts/article_style.py`（`STYLE_PROMPT`・`problems`）・`scripts/quality.py`（`structural_problems`）・`tests/test_article_style.py::EveryPathTest` | |

## サイト

| 番号 | ルール | 日付 | なぜ | 強制 | 点検 |
|---|---|---|---|---|---|
| W1 | ロゴ用の書体（font-display）は決めた部品だけ | 2026-11-03 | 一部の文字だけ書体が変わって崩れる | `tests/test_display_font_usage.cjs`・`scripts/ui_check.mjs` | |
| W2 | ホームのストーリーの丸はコンパクト | 2026-11-02 | 場所を取りすぎる | `tests/test_owner_rules_site.cjs`・`tests/test_story_topics.cjs` | |
| W3 | 壊れたページ・リンクを出さない | 2026-11-03 | 信用を落とす | `scripts/ui_check.mjs`（`ui_check.yml` デプロイ後と毎朝）・見張り `scripts/owner_rules_audit.py` | ● |
| W4 | 広告の PR 表示は小さな1行で必ず出す | 2026-11-03 | 法律で必要・PR 感は出しすぎない | `scripts/ui_check.mjs`（PR 表示なしの提携リンク） | |
| W5 | 連絡先は contact@ai-tech-express.jp | 2026-11-03 | 独自ドメインの窓口 | `site.config.ts`・`tests/test_owner_rules_site.cjs`・見張り `scripts/owner_rules_audit.py` | ● |
| W6 | オーナーの名前は /about と /editorial-policy だけ（記事や全ページの構造化データに入れない） | 2026-11-03 | 個人名を記事に出さない | `lib/publisher.ts`・`tests/test_owner_rules_site.cjs`・見張り `scripts/owner_rules_audit.py` | ● |
| W7 | 自分のアクセスを数えない（`?_internal=1` か `X-Saisoku-Internal: 1`） | 2026-11-03 | 数字が水増しされる | `cloudflare/inflow.ts`・`tests/test_inflow_report.py` | |
| W8 | 嫌いな見た目を使わない（Zen Kaku の書体・AI っぽい「↗」・黄色いマーカー） | 2026-10-21 | オーナーの好み | `tests/test_owner_rules_site.cjs` | |
| W9 | 文字の見やすさの点検は0件 | 2026-10-27 | 読みにくいと離れる | `scripts/check_readability.mjs`（`readability.yml`） | |
| W10 | 同じ広告の枠・メール登録欄は1ページに1つまで（かんたん版と通常版の両方に入れない。広告はすべての記事に出す） | 2026-11-03 | 同じ枠が2つあると広告だらけ・押し売りに見える | `app/articles/[slug]/page.tsx`・`lib/newsletter-placement.ts`・`tests/test_page_slots.cjs`・`scripts/ui_check.mjs`（同じ枠が2つ）・見張り `scripts/owner_rules_audit.py`（`slot_problems`） | ● |
| W11 | 表・料金表・判定・本文に「記載なし」を出さない（値の無い列・行は隠し、残る空欄は空のまま。「—」で埋めない） | 2026-11-03 | 壊れて見える・答えになっていない | `lib/no-data.ts`・`components/PriceTable.tsx`・`lib/article-sections.ts`（表の空欄）・`tests/test_no_data.cjs`・`scripts/ui_check.mjs`（「記載なし」の表示）・見張り `scripts/owner_rules_audit.py`・`tests/test_owner_rules_audit.py::PageTest` | ● |
| W12 | 役に立たないツールのページを置かない。/tools/usd-jpy（ドル建てサブスクの円換算）は終了して 410。ナビ・フッター・サイトマップ・記事の埋め込みからも外す。ツールが1つだけの一覧は作らず /tools は転送 | 2026-11-03 | 「このページもいらない」。料金の段の表の円の目安と重なり、記事への埋め込みも料金の話に合っていなかった | `cloudflare/worker.ts`（`RETIRED_PAGES`）・`lib/calculators.ts`・`next.config.ts`・`tests/test_products_hub.cjs`・`tests/test_charts_calc.cjs`・見張り `scripts/owner_rules_audit.py`（410 とリンクの残り）・`tests/test_owner_rules_audit.py::ProductHubTest` | ● |
| W13 | AIサービス図鑑は中身のある製品だけ載せる（確かめた公式の料金・日付つきの動き・機能がそろわない製品は一覧に出さず noindex）。画像は公式の製品画像か文字の無いブランド色の図形だけ（記事の写真・素材・AI 画像は使わない）。決まった型の問いかけの見出し・「記載なし」・「元記事には」を出さない | 2026-11-03 | 「内容がすごく薄い」。カードが一般論の1文だけで、Codex に関係のない記事の写真、ChatGPT Work に素材のノートPCが出ていた | `lib/product-substance.ts`（`productListing`・`cleanProductText`）・`lib/products.ts`（`productProfile`・`listedProducts`）・`content/product_sources.json`・`components/ProductVisual.tsx`・生成の門番 `scripts/products.py`（`non_answer`）・`tests/test_products_hub.cjs`・`tests/test_products_gate.py`・見張り `scripts/owner_rules_audit.py`（`hub_page_problems`・`listed_product_problems`）・`tests/test_owner_rules_audit.py::ProductHubTest` | ● |
| W21 | AI料金表は各社の公式の料金ページを出典にし、行ごとに出典と確認日を出す（自社の記事を出典にしない。公式ページで確かめられない製品は載せない）。日本で使えるかは確認表 `content/japan_availability.json` にそろえる。円換算の為替はサイト全体で1つ（`content/pricing/_fx.json`・同じ出典・同じ日付）。トップの案内も同じ確認日 | 2026-11-03 | 料金表だけ 1ドル＝150円で、ほかのページ（157.9円）と食い違っていた。根拠が自社の記事で、トップと料金表の日付も違った | `lib/fx.ts`（`getUsdJpy`）・`lib/official-pricing.ts`・`app/ai-pricing/page.tsx`・`components/assets/PricingBoard.tsx`・`tests/test_pricing_fx.cjs`・見張り `scripts/owner_rules_audit.py`（`pricing_page_problems`）・`tests/test_owner_rules_audit.py::PricingGoneLiveTest` | ● |
| W22 | 取り下げた記事の 410 ページはサイトの見た目（ヘッダー・ロゴ・フッター・色）で、丁寧な説明（「掲載を終了しました」）と今も載っている近い記事へのリンクを出す。状態コードは 410・noindex のまま | 2026-11-03 | 素のページで、壊れたサイトに見えた | `components/GoneView.tsx`・`app/articles/[slug]/page.tsx`・`lib/retired.ts`・`cloudflare/worker.ts`（`goneResponse`）・`tests/test_gone_page.cjs`・見張り `scripts/owner_rules_audit.py`（`gone_page_problems`）・`tests/test_owner_rules_audit.py::PricingGoneLiveTest` | ● |
| W23 | ヘッダー下の帯の赤い「速報」は本当の速報だけ（画像の札と同じ判定 A9: `breaking` か公式発表から60分以内。載せてから6時間まで）。ほかは紺の「最新」 | 2026-11-03 | 「19時間前」の記事や速報でない記事に赤い「速報」が付き、速報の重みが下がっていた | `lib/live-line.ts`・`components/LiveLine.tsx`・`components/Header.tsx`・`tests/test_live_line.cjs`・見張り `scripts/owner_rules_audit.py`（`live_line_problems`）・`tests/test_owner_rules_audit.py::PricingGoneLiveTest` | ● |

## 運用

| 番号 | ルール | 日付 | なぜ | 強制 | 点検 |
|---|---|---|---|---|---|
| O1 | オーナーへの返事・報告はすべて日本語 | 2026-11-02 | 英語は読めない。「今後は許さない」 | 会話（`~/.claude/CLAUDE.md`） | |
| O2 | Buffer の呼び出し回数の上限を守る | 2026-11-03 | 回数制限で予約が止まった | `scripts/buffer_budget.py`・`tests/test_buffer_budget.py::test_429_blocks_until_retry_after_without_calling` | |
| O3 | オーナーの名前で投稿・コメントするのは OK をもらってから。新しい種類の投稿は見本を見せて OK をもらってから | 2026-10-25 | 名前を使った発信は本人が決める | 会話（下書きを見せて OK）・新しい投稿経路は `tests/test_sns_rules.py::test_every_posting_api_call_goes_through_the_gate` が止める | |
| O4 | コメントは 10:20〜22:59 だけ | 2026-11-03 | 夜遅いコメントは迷惑 | 手元の点検 `scripts/owner_rules_audit.py --comment-log`・`tests/test_owner_rules_audit.py::SnsTest` | |
| O5 | 進み具合を短くこまめに報告する（何をしているか・どこまで・次に何を） | 2026-11-03 | 待たされている感じがした | 会話 | |
| O6 | 見た目で2回外したら、3〜4案を並べて選んでもらう | 2026-10-21 | 当てずっぽうの繰り返しは時間の無駄 | 会話 | |
| O7 | オーナーに直されたら、この台帳に1行足し、守らせる仕組みを同じ変更で入れる | 2026-11-03 | 「分かったふり」で同じ間違いを繰り返した | `AGENTS.md`・`tests/test_owner_rules_ledger.py` | |
