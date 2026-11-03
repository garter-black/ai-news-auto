"""お知らせ・告知・統計の転載の記事を作らない・残さない（2026-11-03 オーナー決定・台帳 A26）。外部 API は呼ばない。

オーナーの決定: たばこの小売定価の認可・褒章の受章者・地ビールの出荷量・決算発表日の予告・議事要旨・毎年の啓発月間のような
「発表をそのまま書き写しただけ」の記事は取り下げ、これからも作らない（直近7日に SNS に出したものだけ残す）。
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import indexnow  # noqa: E402
import notice_gate  # noqa: E402
import retire_articles  # noqa: E402

NOTICES = [
    ("財務省、製造たばこの小売定価の認可情報を随時公開　日付ごとにPDFで確認可能", "listing"),
    ("国土交通省、令和8年秋の褒章受章者を発表　伝達式は11月18日に開催", "listing"),
    ("地ビール1-8月出荷量は2年連続増　首位はエチゴビール、55.1％は前年割れ", "statistics"),
    ("サイバーエージェント、2026年9月期の通期決算を11月11日15時半に発表へ", "earnings-date"),
    ("財務省が国債のオンチェーン対応に関する研究会の議事要旨を公開　第2回会合まで", "minutes"),
    ("厚労省、11月30日の「年金の日」と11月の「ねんきん月間」で啓発　「ねんきんネット」の活用を呼びかけ", "campaign"),
    ("厚労省、11月1日から「遺族年金受給者実態調査」を実施　オンライン回答にも対応", "campaign"),
    ("総務省、9月の人口移動報告を公表　市区町村間の移動者数は5.6％減", "statistics"),
    ("トヨタ、2026年度上半期の販売・生産・輸出実績を発表　ダイハツ含むグループ全体", "statistics"),
]
KEEP = [
    # 分析・解説を足した記事
    "7-9月期の鉱工業生産、前期比0.3％増で4四半期連続の増産　ニッセイ基礎研が分析",
    "総務省統計局が「統計Today」第210号を公開　社会生活基本調査の読み方を解説",
    # AI の話題（政府の生成AIの利用調査）
    "デジタル庁、政府職員向け生成AI「源内」の利用調査を公表　満足度は71.7％",
    # 決算そのもの（数字がある）・申し込める制度・製品
    "Apple、7〜9月期の売上高が12％増の1145億ドル　同期として過去最高",
    "東京都、特別高圧電力・工業用LPガスの支援金で第7回申請受付　最大750万円",
    "Appleが「M6」搭載の14インチMacBook Proを発表　33万9800円から",
    "JPCERT/CC、圧縮ライブラリ「Brotli」の脆弱性で注意喚起　Googleの修正版へ更新を",
]


class NoticeGateTest(unittest.TestCase):
    def test_owner_examples_are_notices(self):
        for title, kind in NOTICES:
            self.assertEqual(notice_gate.notice_reason({"title": title, "channelSlug": "mof"}), kind, title)

    def test_analysis_ai_and_real_news_pass(self):
        for title in KEEP:
            self.assertIsNone(notice_gate.notice_reason({"title": title}), title)
        # 分析のシンクタンクの解説は、統計の題名でも止めない
        self.assertIsNone(notice_gate.notice_reason({"title": "9月の消費動向調査を公表", "channelSlug": "nli-research"}))
        # 地震の速報・まとめ記事は対象外
        self.assertIsNone(notice_gate.notice_reason({"title": "人口移動報告を公表", "disaster": True}))
        self.assertIsNone(notice_gate.notice_reason({"title": "人口移動報告を公表", "type": "roundup"}))

    def test_publish_gate_blocks_and_does_not_retry(self):
        record = {"title": NOTICES[0][0], "body": "x" * 4000, "genreSlug": "business", "tags": ["財務省"], "verdict": {"status": "available"}}
        self.assertEqual(indexnow.publish_block_reason(record), "notice")
        self.assertNotIn("notice", indexnow.PUBLISH_RETRYABLE)
        self.assertIn("notice", indexnow.PUBLISH_FEEDBACK)
        # 手で「検索に出す」と決めた記事・地震の速報は止めない（publish_block_reason の約束）
        self.assertIsNone(indexnow.publish_block_reason({**record, "indexable": True}))
        self.assertIsNone(indexnow.publish_block_reason({**record, "disaster": True}))

    def test_site_rules_are_exported(self):
        # サイト（lib/verdict.ts）が読む lib/notice-rules.json が、この判定と同じであること
        self.assertEqual(notice_gate.RULES_PATH.read_text("utf-8"), notice_gate.rules_json(),
                         "lib/notice-rules.json が古い。python3 scripts/notice_gate.py --export-rules で書き出す")

    def test_retire_keeps_recent_sns_posts(self):
        now = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
        articles = [
            {"slug": "tobacco", "title": NOTICES[0][0], "channelSlug": "mof"},
            {"slug": "cyberagent", "title": NOTICES[3][0], "channelSlug": "cyberagent-news"},
            {"slug": "apple", "title": KEEP[3], "channelSlug": "apple"},
        ]
        with mock.patch.object(retire_articles, "recent_sns", return_value={"cyberagent"}):
            picked, kept = retire_articles.plan_notice(articles, now)
        self.assertEqual(set(picked), {"tobacco"})
        self.assertEqual(picked["tobacco"]["reason"], "notice-listing")
        self.assertNotIn("redirect", picked["tobacco"])  # 転送先は無い（410）
        self.assertEqual([k["slug"] for k in kept], ["cyberagent"])


if __name__ == "__main__":
    unittest.main()
