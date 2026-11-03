"""見出しの書き方をそろえる後処理（scripts/headline_normalize.py）の検証。外部 API は呼ばない。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import headline_normalize as h  # noqa: E402
import quality  # noqa: E402


class BracketTest(unittest.TestCase):
    def test_product_name_uses_kagi(self):
        self.assertEqual(h.normalize_title("Googleの生成AI『Gemini』に新機能"), "Googleの生成AI「Gemini」に新機能")

    def test_quotes_become_kagi(self):
        self.assertEqual(h.normalize_title("トヨタのランドクルーザー“FJ”を発売"), "トヨタのランドクルーザー「FJ」を発売")

    def test_works_keep_double_kagi(self):
        self.assertEqual(h.normalize_title("劇場アニメ『魔法使いの夜』の公開記念"), "劇場アニメ『魔法使いの夜』の公開記念")
        self.assertEqual(h.normalize_title("『紅の砂漠』が新DLC", {"genreSlug": "entertainment"}), "『紅の砂漠』が新DLC")

    def test_quote_inside_kagi_is_kept(self):
        self.assertEqual(h.normalize_title("「『AI』と働く」講座を開催"), "「『AI』と働く」講座を開催")


class SeparatorTest(unittest.TestCase):
    def test_space_after_japanese_becomes_fullwidth(self):
        self.assertEqual(
            h.normalize_title("NVIDIAがツール公開 AI学習を自動で再開"),
            "NVIDIAがツール公開　AI学習を自動で再開",
        )

    def test_spaces_between_latin_words_stay(self):
        self.assertEqual(h.normalize_title("新AI「Gemini 4 Argon」を発表"), "新AI「Gemini 4 Argon」を発表")
        self.assertEqual(h.normalize_title("Mrs. GREEN APPLEが就任"), "Mrs. GREEN APPLEが就任")

    def test_dashes_bars_and_period(self):
        self.assertEqual(h.normalize_title("AI Gatewayで提供開始 – 画像入力対応"), "AI Gatewayで提供開始　画像入力対応")
        self.assertEqual(h.normalize_title("原産地表示違反｜対象商品"), "原産地表示違反　対象商品")
        self.assertEqual(h.normalize_title("共同展開へ。クラウドを一本化。"), "共同展開へ　クラウドを一本化")

    def test_hyphen_inside_names_stays(self):
        self.assertEqual(h.normalize_title("GPT-5とWi-Fi 7の対応"), "GPT-5とWi-Fi 7の対応")
        self.assertEqual(h.normalize_title("東京―大阪間の新路線"), "東京―大阪間の新路線")


class CompanyTest(unittest.TestCase):
    def test_foreign_companies_in_latin(self):
        self.assertEqual(h.normalize_title("マイクロソフトのCopilot Code登場"), "MicrosoftのCopilot Code登場")
        self.assertEqual(h.normalize_title("グーグルとオープンAIが提携"), "GoogleとOpenAIが提携")
        self.assertEqual(h.normalize_title("ＯｐｅｎＡＩのＧＰＴ－６"), "OpenAIのGPT-6")

    def test_words_that_are_not_companies_stay(self):
        self.assertEqual(h.normalize_title("グーグルマップに新機能"), "グーグルマップに新機能")
        self.assertEqual(h.normalize_title("アマゾン川の調査結果"), "アマゾン川の調査結果")
        self.assertEqual(h.normalize_title("パイナップルの値上げ"), "パイナップルの値上げ")

    def test_japanese_companies_unchanged(self):
        self.assertEqual(h.normalize_title("ソフトバンクとトヨタ自動車が提携"), "ソフトバンクとトヨタ自動車が提携")


class EraTest(unittest.TestCase):
    def test_reiwa_becomes_seireki(self):
        self.assertEqual(h.normalize_title("防災庁が令和8年11月2日に発足"), "防災庁が2026年11月2日に発足")
        self.assertEqual(h.normalize_title("令和元年度の決算"), "2019年度の決算")

    def test_reiwa_kept_when_source_title_uses_it(self):
        record = {"videoTitle": "令和8年度消防防災科学技術研究推進制度の採択"}
        self.assertEqual(h.normalize_title("消防庁、令和8年度の研究課題を採択", record), "消防庁、令和8年度の研究課題を採択")

    def test_era_names_that_are_not_years_stay(self):
        self.assertEqual(h.normalize_title("令和の虎が話題に"), "令和の虎が話題に")

    def test_body_gets_seireki_once(self):
        body = "令和8年度の予算です。令和8年度も続きます。令和8年11月に開始。"
        self.assertEqual(h.add_seireki_in_body(body), "令和8年度（2026年度）の予算です。令和8年度も続きます。令和8年（2026年）11月に開始。")
        self.assertEqual(h.add_seireki_in_body("令和8年（2026年）に開始"), "令和8年（2026年）に開始")


class ApplyTest(unittest.TestCase):
    def test_apply_changes_title_seo_and_body_but_not_facts(self):
        record = {
            "title": "マイクロソフトが『Copilot』に新機能 令和8年から提供",
            "seoTitle": "Copilotの新機能｜使い方",
            "body": "## 概要\n令和8年から提供されます。料金は月30ドルです。",
            "videoTitle": "Introducing new Copilot features",
        }
        changes = h.apply(record)
        self.assertEqual(record["title"], "Microsoftが「Copilot」に新機能　2026年から提供")
        self.assertEqual(record["seoTitle"], "Copilotの新機能　使い方")
        self.assertIn("令和8年（2026年）から提供", record["body"])
        self.assertIn("月30ドル", record["body"])
        self.assertTrue(changes)

    def test_idempotent(self):
        title = "Googleの生成AI「Gemini」に新機能　文書やPDFをチャット内で作成・編集"
        self.assertEqual(h.normalize_title(h.normalize_title(title)), title)


class WarningTest(unittest.TestCase):
    def test_long_title_warns_but_short_does_not(self):
        long_title = "カスタマーサポート大手のZendeskがChatGPT Enterpriseを全社導入 社内で600のAIアプリを自作"
        self.assertTrue(quality.title_warnings({"title": long_title}))
        self.assertEqual(quality.title_warnings({"title": "OpenAIがZendesk事例を公開"}), [])


class StyleGateTest(unittest.TestCase):
    """2026-11-03 オーナー指摘「見出しが不自然で幼い。恥ずかしい」の例（直す前 → 受け入れられる直した後）"""

    # (直す前, 受け入れられる直した後)
    EXAMPLES = [
        ("ChatGPTのOpenAIが電力対策「Grid Protectors」　AI施設が電力逼迫時に使用量を減らす",
         "OpenAI、電力対策「Grid Protectors」を開始　AI施設が電力逼迫時に使用量を削減"),
        ("ClaudeのAnthropicが上場申請書を修正　株は「ANT」で売買予定、創業者側が議決権50.1%",
         "Anthropic、上場申請書を修正　創業者側が議決権50.1%を保持、ティッカーは「ANT」"),
        ("AI半導体のNVIDIAが公開、複雑なAIフレームワークを効率管理する「Workload Map」とは",
         "NVIDIAがAI基盤の管理ツール「Workload Map」を公開　複雑なフレームワークを一元管理"),
        ("MicrosoftのAI秘書「Copilot Cowork」、9人が1日1,211件の更新　AIに書かせる開発の6つの教訓",
         "Microsoftが「Copilot Cowork」の開発手法を公開　9人で1日1,211件を更新"),
        ("Google開発AI「Antigravity」にクラウド開発用プラグイン　AIの勝手な課金操作を防ぐ",
         "Google、AIコーディングツール「Antigravity」にクラウド開発用プラグイン　意図しない課金を防止"),
        ("タスク管理のアトラシアン、マーケティング業務管理部門で3年連続「リーダー」に選出",
         "豪Atlassian、Gartnerのマーケティング業務管理部門で3年連続「リーダー」に"),
        ("Canvaがデザインアプリ「Affinity」iPad版を公開　描く・写真編集・ページ制作が1本に",
         "Canva、iPad版「Affinity」を公開　描画・写真編集・レイアウトを1つのアプリに"),
        ("AI半導体のNVIDIAが学習の自動復旧ツール公開　止まったAI開発を人手なしで再開",
         "NVIDIA、AI学習の自動復旧ツールを公開　障害で止まった学習を自動で再開"),
        ("法人向けオンライン保存「セキュアSAMBA」、Mac用アプリ提供開始　Finderで直接編集",
         "kubell、法人向けストレージ「セキュアSAMBA」にMac用アプリ　Finderから直接編集"),
    ]
    # 機械で外せない（書き直しが要る）もの
    NEEDS_REWRITE = [0, 2, 3, 4, 6, 7, 8]

    def test_after_examples_pass(self):
        for _, after in self.EXAMPLES:
            normalized = h.normalize_title(after)
            self.assertEqual(normalized, after)
            self.assertEqual(h.title_problems(after), [], after)

    def test_before_examples_are_fixed_or_rejected(self):
        for i, (before, _) in enumerate(self.EXAMPLES):
            normalized = h.normalize_title(before)
            if i == 5:  # 知られていない会社の説明は機械では判定しない（書き直しのプロンプトで直す）
                continue
            self.assertTrue(normalized != before or h.title_problems(normalized), before)
        rejected = [i for i, (before, _) in enumerate(self.EXAMPLES) if h.title_problems(h.normalize_title(before))]
        for i in (2, 3, 4, 6, 7, 8):
            self.assertIn(i, rejected, self.EXAMPLES[i][0])

    def test_known_company_descriptor_is_stripped(self):
        self.assertEqual(h.normalize_title("ChatGPTのOpenAIがZendesk事例公開　社内で600アプリ自作"), "OpenAIがZendesk事例公開　社内で600アプリ自作")
        self.assertEqual(h.normalize_title("ClaudeのAnthropicが新薬開発に参入"), "Anthropicが新薬開発に参入")
        self.assertEqual(h.normalize_title("半導体大手のNVIDIA、AI開発ツールを公開"), "NVIDIA、AI開発ツールを公開")
        self.assertEqual(h.strip_known_descriptors("AI半導体大手のNVIDIAは開発者ブログで"), "NVIDIAは開発者ブログで")

    def test_relations_and_company_first_are_kept(self):
        for title in ("競合のGoogleが新モデル", "OpenAIの新AIモデル「GPT-6.1 Terra」", "Googleの「Gemini 4」に新機能",
                      "Amazon傘下のAWSが新機能", "米新興のCresta、音声AIで資金調達"):
            self.assertEqual(h.normalize_title(title), title)
            self.assertEqual(h.title_problems(title), [], title)

    def test_childish_words_outside_brackets(self):
        self.assertTrue(h.title_problems("AIに家事をお任せ　新サービス"))
        self.assertTrue(h.title_problems("止まったAI開発を人手なしで再開"))
        # 製品名の中の言葉は見ない
        self.assertEqual(h.title_problems("リフォームテクノロジー、持株会社化で「交換できるくん」を分社化"), [])

    def test_punctuation_gate(self):
        bad = {
            "法人向け保存「セキュアSAMBA」、Mac用アプリを提供開始": "かっこの直後",
            "OpenAI、「Codex」の利用枠を本体と共通化へ": "かっこの直前",
            "OpenAIが新モデルを公開　　料金は半分": "空白が重なっている",
            "OpenAIが新モデルを公開、": "末尾に句読点",
            "トヨタ(7203)が新型車を発表": "半角の記号",
            "GPT-6の新機能:画像生成に対応": "半角の「:」",
            "新モデルを公開,料金は半分に": "半角の「,」",
        }
        for title, label in bad.items():
            problems = h.title_problems(title)
            self.assertTrue(any(label in p for p in problems), (title, problems))
        for title in ("ChatGPT Proに月最大500ドルのAPI利用枠", "楽天証券の総合口座が1,500万を突破", "Gemini 4: 新機能",
                      "Apple、7〜9月期売上高が1145億ドルで過去最高"):
            self.assertEqual(h.punctuation_problems(title), [], title)

    def test_comma_before_bracket_becomes_ga(self):
        self.assertEqual(h.normalize_title("Google、「Gemini Live API」に新機能"), "Googleが「Gemini Live API」に新機能")
        self.assertEqual(h.normalize_title("トヨタとWaymo、「自動運転タクシー」を日本で"), "トヨタとWaymoが「自動運転タクシー」を日本で")
        # 名前でない頭・かっこが後ろにあるものは変えない
        self.assertEqual(h.normalize_title("2026年、「AI元年」の終わり"), "2026年、「AI元年」の終わり")
        self.assertEqual(h.normalize_title("OpenAI、新AIモデル「GPT-6.1 Terra」を公開"), "OpenAI、新AIモデル「GPT-6.1 Terra」を公開")

    def test_fullwidth_comma_and_double_subtitle(self):
        self.assertEqual(h.normalize_title("Sakana AI，自社製の言語モデルを発表"), "Sakana AI、自社製の言語モデルを発表")
        self.assertEqual(h.normalize_title("AWS，「Amazon Bedrock」の運用手法を公開"), "AWSが「Amazon Bedrock」の運用手法を公開")
        self.assertTrue(any("全角空白が2つ" in p for p in h.title_problems("A社が新製品を発表　価格は1万円　発売は12月")))

    def test_quality_gate_warns_on_style(self):
        warnings = quality.title_warnings({"title": "Google開発AI「Antigravity」に新プラグイン"})
        self.assertTrue(any("書き方" in w for w in warnings))


class PromptStyleTest(unittest.TestCase):
    """記事を書くプロンプト・書き直し・SNS の要約が、説明の押し付けを勧めていない"""

    FILES = ["scripts/main.py", "scripts/web_articles.py", "scripts/rewrite_titles.py", "scripts/threads_hooks.py"]

    def test_no_descriptor_recommendation(self):
        for name in self.FILES:
            text = (ROOT / name).read_text("utf-8")
            self.assertNotIn("正体を添える", text, name)
            self.assertNotIn("正体を一言で", text, name)
            self.assertNotIn("良い例:「ChatGPTのOpenAI", text, name)
            self.assertNotIn("お任せ」", text.replace("お任せ／", ""), name)

    def test_templates_use_shared_style(self):
        for name, count in (("scripts/main.py", 1), ("scripts/web_articles.py", 3)):
            text = (ROOT / name).read_text("utf-8")
            self.assertEqual(text.count("- title: <<TITLE_STYLE>>"), count, name)
            self.assertEqual(text.count('.replace("<<TITLE_STYLE>>", headline_normalize.TITLE_STYLE_PROMPT)'), count, name)
        self.assertNotIn("{", h.TITLE_STYLE_PROMPT)  # .format() のテンプレートに入るので { } を使わない
        for _, after in StyleGateTest.EXAMPLES[:1]:
            self.assertEqual(h.title_problems(after), [])


class LiveCorpusTest(unittest.TestCase):
    """公開中の記事の見出しに、よく知られた会社への説明（機械で外せる型）が残っていない"""

    def test_no_known_company_descriptor(self):
        import json
        bad = []
        for path in sorted((ROOT / "content" / "articles").glob("*.json")):
            record = json.loads(path.read_text("utf-8"))
            title = str(record.get("title") or "")
            if h.strip_known_descriptors(title) != title:
                bad.append(f"{path.stem}: {title}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
