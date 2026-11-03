"""ジャンル・タグの付け間違いの判定（scripts/labels.py。台帳 A30）。例は 2026-11-03 の点検で見つかった本物の付け間違い。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import labels  # noqa: E402


def fix(title, genre, tags=()):
    got = labels.genre_fix({"title": title, "genreSlug": genre, "tags": list(tags)})
    return got[0] if got else None


class GenreTest(unittest.TestCase):
    def test_non_ai_articles_leave_ai_tab(self):
        # AI の話ではないのに「AI・テクノロジー」だった
        self.assertEqual("tech-gadget", fix("kubell、法人向けストレージ「セキュアSAMBA」にMac用アプリ　Finderから直接編集", "ai",
                                            ["セキュアSAMBA", "オンラインストレージ", "Mac", "クラウド", "法人向けIT"]))
        self.assertEqual("tech-gadget", fix("サムスン、初のカフ型イヤホン「Galaxy Buds On」を発表　229ドルで11月6日発売", "ai", ["Samsung", "イヤホン"]))
        self.assertEqual("tech-gadget", fix("Apple、古いiPhone向け「iOS 18.7.11」を公開　悪用報告のある脆弱性など3件を修正", "ai", ["iPhone", "セキュリティ"]))
        self.assertEqual("science", fix("NASA、有人月周回ミッション「アルテミスII」の試験施設を公開　環境試験を紹介", "ai", ["Google", "宇宙開発"]))

    def test_ai_articles_stay(self):
        self.assertIsNone(fix("Apple、新しい「Siri AI」を日本語で提供開始　画面や写真の内容も理解", "ai"))
        self.assertIsNone(fix("Qodo、AIコーディングツール「Codex」向けプラグインを公開", "ai"))
        self.assertIsNone(fix("NVIDIAが「Nemotron 3.5 Transcribe」を公開　話者識別にも対応", "ai", ["NVIDIA", "音声認識"]))

    def test_earnings_go_to_investment(self):
        # Apple の決算が「テクノロジー・ガジェット」だった（決算は投資・マネー。台帳 A23）
        self.assertEqual("investment", fix("Apple、7〜9月期の売上高が12％増の1145億ドル　同期として過去最高", "tech-gadget"))
        # 新製品の発表は動かさない
        self.assertIsNone(fix("Apple、M6チップ搭載の新iMacを発表　Intel製モデル比で最大18倍高速、24万9800円から", "tech-gadget"))

    def test_public_bodies_go_to_public(self):
        # IPA（独立行政法人）の公表・手引きが「テクノロジー・ガジェット」だった
        self.assertEqual("public", fix("IPA、ITパスポート合格者の学習実態調査を公表　学習期間は1〜2カ月未満が最多", "tech-gadget"))
        self.assertEqual("public", fix("IPAと経産省、サプライチェーン共通編の2.0版を公開　データ連携ガイドラインの手引きを改定", "tech-gadget"))
        self.assertEqual("public", fix("片山財務相、12月1日から免税をリファンド方式に移行と説明　ガソリン補助は予備費1兆1,481億円を使用", "business"))
        # 民間企業はそのまま
        self.assertIsNone(fix("KDDIとWing、ドローン配送で提携　2027年中に食料品や日用品の配送開始を目指す", "tech-gadget"))
        # 公的機関でも AI の話なら AI のまま
        self.assertIsNone(fix("デジタル庁、政府職員向け生成AI「源内」の利用調査を公表　満足度は71.7％", "ai"))

    def test_money_topics_route_like_candidates(self):
        # 候補の振り分け（genre_focus.route_section）と同じ判定
        self.assertEqual("investment", fix("ECB、政策金利の据え置きを全会一致で決定　次回12月の利上げを示唆", "business"))

    def test_fixed_genres_are_not_moved(self):
        self.assertIsNone(fix("【地震】千葉県北東部で最大震度5弱、M5.4（気象庁）", "public"))
        self.assertIsNone(fix("フットサル日本代表、11月6日・8日に大分でモロッコ代表と親善試合", "sports"))

    def test_generation_uses_the_same_rule(self):
        src = (Path(__file__).resolve().parent.parent / "scripts" / "web_articles.py").read_text("utf-8")
        self.assertIn("labels.genre_fix(", src)
        self.assertIn("labels.normalize_tags(", src)


class SubjectTest(unittest.TestCase):
    def test_subject_is_before_first_comma_or_ga(self):
        self.assertEqual("Qodo", labels.subject_of("Qodo、AIコーディングツール「Codex」向けプラグインを公開"))
        self.assertEqual("NVIDIA", labels.subject_of("NVIDIAが「Nemotron 3.5」を公開"))
        self.assertEqual("「Google Messages」のベータ版", labels.subject_of("「Google Messages」のベータ版、アイコンが一新"))
        self.assertEqual("", labels.subject_of("AIエージェント実用化の3技術を整理"))


class TagTest(unittest.TestCase):
    def test_company_names_and_spelling_are_unified(self):
        self.assertEqual(["Microsoft", "Azure"], labels.normalize_tags(["マイクロソフト", "Azure", "Microsoft"]))
        self.assertEqual(["Atlassian", "Jira"], labels.normalize_tags(["アトラシアン", "Jira"]))
        self.assertEqual(["Samsung", "テレビ"], labels.normalize_tags(["サムスン", "テレビ", "Samsung"]))
        self.assertEqual(["サイバーセキュリティ"], labels.normalize_tags(["サイバーセキュリティー", "サイバーセキュリティ"]))


if __name__ == "__main__":
    unittest.main()
