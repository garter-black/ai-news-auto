"""SNS 動画の「中身に合わせた型」（scripts/video_templates.py）のテスト。

- 型の選び方（template_for）が記事 JSON だけで決まる（LLM・ネットなし）
- 既定（SNS_VIDEO_TEMPLATES が無い・off）は今の承認済みの型のまま（shorts_v3.make_versions が型を使わない）
- カードの中身は記事にある事実だけ（比較は charts の rivals だけ・A7。写真は公式のときだけ・I1/I2）
- 速報の SNS 版は 15〜25秒の範囲で作る
- 合成の「公式ページ」とテスト用の合成音で、各カードを 480x270・10fps に実際に書き出し、品質ゲートに通る
  （ffmpeg / numpy / Pillow / budoux が無ければ書き出しのテストだけ飛ばす）

  python3 -m unittest tests/test_video_templates.py -v
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import video_templates as vt  # noqa: E402  (型の選び方は numpy なしで動く)

try:
    import budoux  # noqa: F401
    import numpy as np
    from PIL import Image

    import shorts_v3 as v3
    import test_shorts_v3 as t3
    import video_gate as gate

    READY = shutil.which("ffmpeg") is not None
except ImportError:  # pragma: no cover
    READY = False


def art(**kw) -> dict:
    base = {"slug": "x", "title": "", "lede": "", "description": "", "highlights": [], "tags": [], "keywords": [], "body": ""}
    base.update(kw)
    return base


SECURITY = art(
    title="Example社、クラウドの不正利用に警告　1日で10万ドル超の被害も",
    lede="Example社は、AIのAPIキーが悪用される攻撃について警告しました。",
    tags=["セキュリティ"],
    body="## 概要\n\n北米や日本の金融、小売の分野で被害が確認されています。短時間で多額のクラウド料金が発生する点が特徴です。\n\n"
    "## まとめ\n\n管理者は、APIキーの管理と権限の最小化を徹底してください。不審なリクエストがないかを確認することが重要です。",
)
EARNINGS = art(
    title="Example社、7〜9月期の売上高が12％増の1145億ドル",
    lede="Example社は7〜9月期の決算を発表しました。売上高は前年同期比12%増の1,145億ドルでした。",
    body="## スペックと価格は？\n\n| 項目 | 内容 | 出典 |\n| --- | --- | --- |\n| 7〜9月期売上高 | 1,145億ドル（前年同期比12%増） | 公式 |\n"
    "| 通期売上高 | 4,790億ドル | 公式 |\n| 希薄化後EPS | 2.10ドル（前年同期比14%増） | 公式 |\n",
)
PRICING = art(
    title="Example AI、Proの月額API利用枠を明記　最大500ドル分",
    tags=["料金プラン"],
    body="## 料金は？\n\n- Pro 100：毎月100ドル分のAPIクレジット\n- Pro 200：毎月200ドル分のAPIクレジット\n- Pro 500：毎月500ドル分のAPIクレジット\n",
)
RIVALS = art(
    title="Example、初の電動アシストMTB「R1」を公開",
    breaking=True,
    charts=[{"kind": "rivals", "title": "同じクラスの製品との比較", "source": {"name": "各社の公式ページ"}, "groups": [
        {"label": "重さ", "unit": "kg", "items": [{"label": "Example R1", "value": 24.0, "display": "約24kg", "highlight": True}, {"label": "Other T4", "value": 24.41, "display": "24.41kg"}]},
    ]}],  # fmt: skip
)
MONEY = art(
    title="Salesforce、音声AI開発の米新興Crestaを買収へ　電話対応を自動化",
    lede="Salesforceは買収の最終合意を発表しました。買収の完了は2028会計年度の第1四半期を予定しています。",
)
LAUNCH = art(
    title="サムスン、新しいテレビ「Micro RGB TV」を発表",
    imageCredit={"kind": "official", "org": "Samsung Newsroom", "original": "https://example.com/a.jpg"},
    thumbnail="https://ai-tech-express.jp/img/x.jpg",
    body="## 前と何が違う？\n\n### 微細RGB LEDで色精度が向上\n本文\n\n### AIエンジンが映像を自動最適化\n本文\n",
)
BREAKING = art(title="Example、新しいアプリを公開", breaking=True)


class ClassifierTest(unittest.TestCase):
    def test_types(self):
        self.assertEqual(vt.template_for(SECURITY), "security")
        self.assertEqual(vt.template_for(EARNINGS), "earnings")
        self.assertEqual(vt.template_for(PRICING), "pricing")
        self.assertEqual(vt.template_for(RIVALS), "comparison")  # 速報でも本物の比較データがあれば比較
        self.assertEqual(vt.template_for(MONEY), "policy_money")
        self.assertEqual(vt.template_for(LAUNCH), "launch")
        self.assertEqual(vt.template_for(BREAKING), "breaking")
        self.assertEqual(vt.template_for(art(title="Example社が社名を変更")), "default")

    def test_deterministic_and_known(self):
        for a in (SECURITY, EARNINGS, PRICING, RIVALS, MONEY, LAUNCH, BREAKING):
            got = {vt.template_for(copy.deepcopy(a)) for _ in range(3)}
            self.assertEqual(len(got), 1)
            self.assertIn(got.pop(), vt.TEMPLATES)

    def test_needs_real_data(self):
        # 比較の語があっても、記事に比較データ（charts の rivals）が無ければ比較の型にしない（A7）
        self.assertNotEqual(vt.template_for(art(title="Example、他社と比較してベンチマークで上回る")), "comparison")
        # 決算の語だけ（数字が無い）なら決算の型にしない
        self.assertNotEqual(vt.template_for(art(title="Example社、決算を11月11日に発表へ")), "earnings")
        # 公式でない写真（AI・素材）は写真のカードに使わない（I1/I2）
        self.assertIsNone(vt.official_image(art(imageCredit={"kind": "ai", "model": "flux"}, thumbnail="https://ai-tech-express.jp/img/y.jpg")))
        self.assertIsNone(vt.official_image(art(imageCredit={"kind": "stock"}, thumbnail="https://ai-tech-express.jp/img/y.jpg")))

    def test_card_data_comes_from_article(self):
        rows = vt.figure_rows(EARNINGS)
        self.assertEqual(rows[0], {"label": "7〜9月期売上高", "value": "1,145億ドル", "change": "前年同期比12%増"})
        self.assertEqual([r["name"] for r in vt.price_rows(PRICING)], ["Pro 100", "Pro 200", "Pro 500"])
        sec = {r["label"]: r["text"] for r in vt.security_rows(SECURITY)}
        self.assertEqual(set(sec), {"影響", "対象", "今すること"})
        self.assertIn("料金が発生", sec["影響"])
        money = {r["label"]: r["text"] for r in vt.policy_rows(MONEY)}
        self.assertEqual(money["誰が"], "Salesforce")
        self.assertEqual(money["いつ"], "2028会計年度の第1四半期")
        self.assertNotIn("いくら", money)  # 記事に金額が無ければ描かない
        self.assertEqual(vt.official_image(LAUNCH)["credit"], "画像：Samsung Newsroom（公式発表より）")
        self.assertEqual(vt.feature_lines(LAUNCH), ["微細RGB LEDで色精度が向上", "AIエンジンが映像を自動最適化"])


class SwitchTest(unittest.TestCase):
    def test_default_is_off(self):
        self.assertFalse(vt.enabled({}))
        self.assertFalse(vt.enabled({"SNS_VIDEO_TEMPLATES": "off"}))
        self.assertTrue(vt.enabled({"SNS_VIDEO_TEMPLATES": "on"}))
        self.assertEqual(vt.APPROVED, "default")

    @unittest.skipUnless(READY, "numpy / Pillow / budoux / ffmpeg が必要")
    def test_make_versions_default_is_current_template(self):
        """スイッチが無い・off のときは、記事が速報・セキュリティでも今の承認済みの型（型の処理を通らない）"""
        calls = []
        with mock.patch.dict(os.environ, {"SNS_VIDEO_TEMPLATES": ""}), \
             mock.patch.object(v3.shorts, "load_article", return_value=dict(SECURITY, slug="s")), \
             mock.patch.object(v3, "prepare_versions", side_effect=lambda *a, **k: calls.append(("prepare", k)) or ([], None, "テスト")), \
             mock.patch.object(vt, "make_versions", side_effect=lambda *a, **k: self.fail("既定で型を使ってはいけない")):  # fmt: skip
            out = v3.make_versions("s", Path(tempfile.mkdtemp()))
        self.assertEqual(calls, [("prepare", {})])  # 今までと同じ呼び方（型の一言も台本の別ファイルも無し）
        self.assertEqual(out["full"]["failReason"], "テスト")
        with mock.patch.dict(os.environ, {"SNS_VIDEO_TEMPLATES": "on"}), \
             mock.patch.object(v3.shorts, "load_article", return_value=dict(SECURITY, slug="s")), \
             mock.patch.object(vt, "make_versions", return_value={"full": {"t": 1}, "short": {"t": 1}}) as tm:  # fmt: skip
            v3.make_versions("s", Path(tempfile.mkdtemp()))
        self.assertEqual(tm.call_args.args[2], "security")


class ScriptTest(unittest.TestCase):
    SCRIPT = {
        "hook": {"text": "問い", "payoff": "", "speech": "あ" * 18},
        "beats": [{"label": "答え", "keyword": "k1", "speech": "い" * 40}, {"label": "プランの比較", "keyword": "k2", "speech": "う" * 40},
                  {"label": "必要な人", "keyword": "k3", "speech": "え" * 40}, {"label": "確かめ方", "keyword": "k4", "speech": "お" * 40}],  # fmt: skip
        "compare": {"beat": 2, "base": "a", "subject": "b", "ratio": "5倍"},
        "short_beats": [1, 2],
        "conclusion": {"text": "c", "speech": "か" * 20},
    }

    def test_breaking_is_short(self):
        s = vt.breaking_script(self.SCRIPT)
        self.assertEqual(s["_length_range"], [15.0, 25.0])
        self.assertEqual(s["_variant"], "short")
        self.assertEqual(len(s["beats"]), 1)  # 18+40+40+31 > 100 → 答えの場面だけ
        self.assertEqual(s["compare"]["beat"], 0)  # 落とした場面のグラフは出さない
        self.assertIn("リンク", s["conclusion"]["speech"])  # 温かく記事のリンクへ誘う締め

    def test_decorate_avoids_busy_beats(self):
        s = vt.decorate("security", SECURITY, self.SCRIPT)
        cards = s["cards"]
        self.assertEqual([c["mode"] for c in cards], ["checklist"])
        self.assertEqual(cards[0]["beat"], 4)  # 「確かめ方」の場面
        self.assertEqual(vt.decorate("default", SECURITY, self.SCRIPT).get("cards"), None)
        launch = vt.decorate("launch", LAUNCH, self.SCRIPT, image_path="/tmp/x.jpg")
        self.assertEqual([(c["mode"], c["beat"]) for c in launch["cards"]], [("image", 1), ("specs", 3)])  # 2 は比較のグラフがある
        self.assertEqual(launch["_image_credit"], "画像：Samsung Newsroom（公式発表より）")
        self.assertNotIn("cards", vt.decorate("launch", dict(LAUNCH, body=""), self.SCRIPT, image_path=""))  # 写真が取れない・特長が無い → 足さない


class LabelTest(unittest.TestCase):
    """2026-11-04 オーナー: 左の列の見出し・キーワードが「答え クラスター」×4・「前と比べると Recovery」で意味が分からない"""

    def setUp(self):
        import importlib

        try:
            self.gate = importlib.import_module("video_gate")
        except ImportError:  # pragma: no cover
            self.skipTest("video_gate を読めない")

    def test_bare_phrase(self):
        for bad in ("クラスター", "Recovery", "NVIDIA Recovery", "実績", "注意", "オープンソース", ""):
            self.assertTrue(self.gate.bare_phrase(bad), bad)
        for ok in ("何ができる？", "検知まで平均69秒", "人手から自動へ", "止まっても自動で再開", "世界初公開", "金融・小売・交通", "学習の復旧", "GPU故障", "答えは？"):
            self.assertFalse(self.gate.bare_phrase(ok), ok)

    def test_label_problems(self):
        nvidia = {"beats": [{"label": "答え", "keyword": "クラスター"}, {"label": "答え", "keyword": "クラスター"},
                            {"label": "前と比べると", "keyword": "Recovery"}, {"label": "答え", "keyword": "クラスター"}]}  # fmt: skip
        probs = self.gate.label_problems(nvidia)
        joined = " ".join(probs)
        self.assertIn("beat2 の見出し「答え」が beat1 と同じ", joined)
        self.assertIn("beat2 のキーワード「クラスター」が beat1 と同じ", joined)
        self.assertIn("キーワード「Recovery」が名詞1つだけ", joined)
        good = {"beats": [{"label": "何ができる？", "keyword": "学習を自動で再開"}, {"label": "どれくらい速い？", "keyword": "検知まで平均69秒"},
                          {"label": "前と何が違う？", "keyword": "人手から自動へ"}]}  # fmt: skip
        self.assertEqual(self.gate.label_problems(good), [])


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class IntroTest(unittest.TestCase):
    def test_short_versions_start_with_what_it_is(self):
        """SNS 用の短い版・速報の版も beat 1（これは何？）から始まる"""
        s = copy.deepcopy(ScriptTest.SCRIPT)
        s["beats"][0]["label"] = "何ができる？"
        s["short_beats"] = [2, 3]
        short = v3.short_script(s)
        self.assertEqual(short["beats"][0]["label"], "何ができる？")
        self.assertEqual([b["label"] for b in short["beats"]], ["何ができる？", "プランの比較", "必要な人"])
        self.assertEqual(short["compare"]["beat"], 2)  # 付け替え
        brk = vt.breaking_script(s)
        self.assertEqual(brk["beats"][0]["label"], "何ができる？")
        self.assertIn("これは何？", v3.script_system())  # 台本の決まりにも書いてある


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class LintTest(unittest.TestCase):
    def test_cards_have_no_lines_or_bars(self):
        self.assertEqual(gate.lint_renderer(ROOT / "scripts" / "video_templates.py"), [])
        self.assertEqual(gate.lint_renderer(ROOT / "scripts" / "shorts_v3.py"), [])

    def test_modes_match(self):
        self.assertEqual(tuple(v3.TEMPLATE_CARD_MODES), tuple(vt.CARD_MODES))
        for pattern, _ in v3.PRODUCT_PAGES:  # 料金ページを撮る製品は、型を選ぶ側でも料金の型の候補
            self.assertTrue(all(vt.PRICING_PRODUCTS.search(w) for w in pattern.pattern.split("|")), pattern.pattern)


def _photo(path: Path) -> str:
    """公式の写真の代わり（グラデーションと丸）。テストではネットに出ない"""
    yy, xx = np.mgrid[0:630, 0:1200]
    img = np.stack([(xx / 1200 * 200 + 30), (yy / 630 * 160 + 40), np.full_like(xx, 180)], axis=-1).astype(np.uint8)
    img[(xx - 600) ** 2 + (yy - 315) ** 2 < 120**2] = (240, 240, 250)
    rng = np.random.default_rng(7)  # 本物の写真のような細かい模様（なめらかな絵だと寄っても差が出にくい）
    for _ in range(160):
        x, y, r = int(rng.integers(0, 1200)), int(rng.integers(0, 630)), int(rng.integers(8, 40))
        img[(xx - x) ** 2 + (yy - y) ** 2 < r**2] = rng.integers(0, 255, 3)
    Image.fromarray(img).save(path)
    return str(path)


@unittest.skipUnless(READY, "ffmpeg / numpy / Pillow / budoux が必要")
class CardRenderTest(unittest.TestCase):
    """各カードを小さく書き出して、品質ゲート（test プロファイル＋v3 の基準）に通るか"""

    @classmethod
    def setUpClass(cls):
        cls._saved = (v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE)
        v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE = 24.0, 42.0, (20.0, 45.0)  # 合成の4場面の台本なので短い範囲
        cls.tmp = Path(tempfile.mkdtemp())
        a = t3.article()
        # カードの中身は記事の文字だけ（fixture の記事にある数字: 3000円・5倍・2026年11月）
        cards_a = [
            {"beat": 1, "mode": "image", "title": "公式発表の写真", "source": "画像：Fixture AI（公式発表より）", "rows": [], "image": _photo(cls.tmp / "p.png"), "focus": [0.5, 0.5]},
            {"beat": 3, "mode": "specs", "title": "主な特長", "source": "記事より", "rows": [{"name": "利用量は通常プランの5倍"}, {"name": "日本でも2026年11月から提供"}]},
            {"beat": 4, "mode": "checklist", "title": "影響・対象・今すること", "source": "記事より", "rows": [{"label": "対象", "text": "毎日たくさん使う人"}, {"label": "今すること", "text": "公式サイトで料金を確かめる"}]},
        ]
        cards_b = [
            {"beat": 1, "mode": "timeline", "title": "誰が・いつ・いくら", "source": "記事より", "rows": [{"label": "誰が", "text": "Fixture AI"}, {"label": "いつ", "text": "2026年11月"}, {"label": "いくら", "text": "月額3000円", "note": "Pro Max"}]},
            {"beat": 3, "mode": "figures", "title": "主な数字", "source": "記事より", "rows": [{"label": "月額", "value": "3000円"}, {"label": "利用量", "value": "5倍", "change": "通常プランと比べて"}]},
            {"beat": 4, "mode": "versus", "title": "比べると", "source": "記事より", "rows": [{"label": "利用量", "unit": "", "items": [{"label": "Pro Max", "value": 5, "display": "5倍", "highlight": True}, {"label": "通常プラン", "value": 1, "display": "基準"}]}]},
        ]
        cls.reports = {}
        for name, cards, tpl in (("a", cards_a, "launch"), ("b", cards_b, "policy_money")):
            script = copy.deepcopy(t3.SCRIPT)
            script.update({"cards": cards, "_template": tpl})
            if name == "a":
                script["_image_credit"] = "画像：Fixture AI（公式発表より）"
            cls.reports[name] = v3.make_video(f"fixture-ai-plan", cls.tmp / name, tts="fake", article=a, pages=[t3.fake_page()], script=script,
                                              profile="test", fps=10, out_scale=0.25, workers=2, sheet=False)  # fmt: skip
        brk = vt.breaking_script(dict(copy.deepcopy(t3.SCRIPT), short_beats=[2, 4]))
        cls.reports["breaking"] = v3.make_video("fixture-ai-plan", cls.tmp / "brk", tts="fake", article=a, pages=[t3.fake_page()], script=brk,
                                                profile="test", fps=10, out_scale=0.25, workers=2, sheet=False)  # fmt: skip

    @classmethod
    def tearDownClass(cls):
        v3.TARGET_MIN, v3.TARGET_MAX, v3.LENGTH_RANGE = cls._saved
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_cards_pass_gate(self):
        for name in ("a", "b"):
            r = self.reports[name]
            self.assertEqual(r["errors"], [], name)
            self.assertTrue(r["gate"]["ok"], (name, r["gate"]["reasons"]))
            modes = sorted(L["mode"] for L in r["ladders"])
            self.assertEqual(modes, sorted({"a": ["image", "specs", "checklist"], "b": ["timeline", "figures", "versus"]}[name]), name)

    def test_image_credit_shown(self):
        r = self.reports["a"]
        self.assertEqual(r["image_credit"], "画像：Fixture AI（公式発表より）")
        self.assertIn(r["image_credit"], r["credits_shown"])
        self.assertIn(r["image_credit"], r["description"])
        self.assertEqual(r["template"], "launch")

    def test_breaking_length(self):
        r = self.reports["breaking"]
        self.assertEqual(r["length_range"], [15.0, 25.0])
        self.assertTrue(15.0 <= r["gate"]["metrics"]["duration"] <= 25.0, r["gate"]["metrics"].get("duration"))
        self.assertTrue(r["gate"]["ok"], r["gate"]["reasons"])
        self.assertEqual(r["min_changes"], vt.BREAKING_MIN_CHANGES)
        self.assertGreaterEqual(r["gate"]["metrics"]["v3_changes"], vt.BREAKING_MIN_CHANGES)

    def test_repeated_labels_fail_gate(self):
        bad = copy.deepcopy(self.reports["b"])
        for b in bad["script_v3"]["beats"]:
            b["label"] = "答え"
        bad["script_v3"]["beats"][0]["keyword"] = "クラスター"
        res = gate.check_video(self.tmp / "b" / "fixture-ai-plan__v3.mp4", bad, t3.article(), "test")
        self.assertFalse(res.ok)
        self.assertTrue(any("見出し「答え」が beat1 と同じ" in x for x in res.reasons), res.reasons)
        self.assertTrue(any("「クラスター」が名詞1つだけ" in x for x in res.reasons), res.reasons)

    def test_card_numbers_are_checked(self):
        """カードに記事に無い数字を描いたら落ちる（rendered_strings の照合）"""
        bad = copy.deepcopy(self.reports["b"])
        bad["rendered_strings"].append("月額4000円")
        res = gate.check_video(self.tmp / "b" / "fixture-ai-plan__v3.mp4", bad, t3.article(), "test")
        self.assertFalse(res.ok)
        self.assertTrue(any("4000" in x for x in res.reasons), res.reasons)


if __name__ == "__main__":
    unittest.main()
