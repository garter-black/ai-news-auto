"""SNS 動画 v3 の台本の段が自分で直せるかのテスト（2026-11-03 の nvidia-recovery の失敗を受けて）。

- 読み上げの合計が長すぎる → 大事でない文を機械で削って、検査に通す（LLM を呼ばない）
- 証拠の文にキーワードが無い → 画面の文字から選び直すか、証拠の文（訳）にある記事の言葉をキーワードにする（言葉は作らない）
- それでも直らない問題は、具体的な直し方（字数の予算・キーワードの候補）を付けて LLM に書き直させる
- 書き直しは時間の上限（SCRIPT_TIME_CAP）の中で。上限を超えそうなら次の LLM を呼ばない

  python3 -m unittest tests.test_shorts_v3_script -v
ネット・LLM・VOICEVOX・ブラウザは使わない（LLM は偽物）。numpy / Pillow / budoux が無ければ飛ばす。
"""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import budoux  # noqa: F401
    import numpy as np

    import shorts_v3 as v3
    import video_gate as gate

    READY = True
except ImportError:  # pragma: no cover
    READY = False


ARTICLE = {
    "slug": "fixture-recovery",
    "sourceType": "web",
    "sourceUrl": "https://example.com/blog/fixture-recovery",
    "sourceName": "Fixture Developer Blog",
    "title": "FixtureがAI学習の自動復旧ツール「Fixture Recovery」を公開　止まった学習を自動で再開",
    "lede": "Fixtureは、AI学習の中断を自動で検知し復旧するオープンソースの「Fixture Recovery」を紹介しました。最初は見るだけの監視モードで試せます。",
    "description": "Fixtureが公開したAI学習の自動復旧ツール「Fixture Recovery」を解説します。",
    "highlights": ["Fixture Recoveryは学習の中断を自動で復旧するオープンソース", "異常の検知までの平均は69秒", "最初は監視モードで始まる"],
    "verdict": {"status": "available", "japan": "unknown", "price": "オープンソース", "free": True, "audience": "AI基盤担当", "take": "GPUクラスターを運用する人は、まず監視モードで試すのが現実的です。"},
    "body": (
        "## 概要\n\nFixtureが、AIの学習が途中で止まったときに自動で直すツール「Fixture Recovery」を紹介しました。\n\n"
        "従来は中断のたびに、人が手作業でログを調べて復旧していました。数千台のGPUで学習を回すと、手作業の復旧は追いつきません。\n\n"
        "Fixture Recoveryは異常を見つけ、原因の機械を外し、保存した地点から再開させます。異常の検知までの平均は69秒でした。\n\n"
        "最初は監視モードで始まり、勝手に操作することはありません。ソースコードはGitHubで公開されています。"
    ),
    "easy": "## ひとことで言うと\n\nFixtureが、AIの学習が止まったときに自動で再開させる無料のツールを紹介しました。",
    "genre": "AI・テクノロジー",
    "genreSlug": "ai",
    "tags": ["Fixture", "Fixture Recovery"],
    "imageCredit": {"kind": "official", "org": "Fixture", "sourceUrl": "https://example.com/blog/fixture-recovery"},
    "publishedAt": "2026-11-02T22:43:05+00:00",
}

ROWS = [
    ("Keep Distributed Training Running with Automated Workload Recovery", 40),
    ("Manual recovery becomes unsustainable when jobs run on thousands of GPUs.", 18),
    ("Fixture Recovery detects anomalies in 69 seconds on average.", 18),
    ("It starts in observe-only mode by default.", 18),
    ("The source code is available on GitHub.", 18),
]


def fake_page() -> "v3.Page":
    """英語の公式ブログらしいページ（文字と1文字ずつの枠だけ。台本の検査に絵は要らない）"""
    blocks = []
    y = 100
    for i, (text, size) in enumerate(ROWS, 1):
        chars = [(ch, (40 + j * size * 0.55, y, 40 + (j + 1) * size * 0.55, y + size * 1.3) if ch.strip() else None) for j, ch in enumerate(text)]
        blocks.append(v3.Block(f"1-{i}", text, (40, y, 40 + len(text) * size * 0.55, y + size * 1.3), chars, float(size)))
        y += 120
    return v3.Page(ARTICLE["sourceUrl"], "example.com", np.zeros((900, 540, 3), dtype=np.uint8), blocks, "Fixture Developer Blog")


def beat(label, keyword, sub, speech, block, target, ja):
    return {"label": label, "keyword": keyword, "sub": sub, "speech": speech, "block": block, "target": target, "ja": ja}


GOOD = {
    "hook": {"text": "AI学習が止まったら誰が直す？", "payoff": "69秒", "speech": "エーアイ学習が止まったら、誰が直しますか？", "block": "1-3",
             "target": "Fixture Recovery detects anomalies in 69 seconds on average.", "ja": "Fixture Recoveryは平均69秒で異常を検知します。"},
    "beats": [
        beat("答え", "自動で復旧", "人の代わりに", "答えは、ツールが自動で復旧することです。異常を見つけて、保存した地点から再開させます。",
             "1-2", "Manual recovery becomes unsustainable when jobs run on thousands of GPUs.", "数千台のGPUで学習を回すと、手作業の復旧は追いつきません。"),
        beat("前と比べると", "検知まで69秒", "異常の検知の平均", "異常の検知までの平均は69秒でした。夜中でも、人を待たずに動き出します。",
             "1-3", "Fixture Recovery detects anomalies in 69 seconds on average.", "Fixture Recoveryは平均69秒で異常を検知します。"),
        beat("必要な人", "数千台のGPU", "学習を回す担当", "数千台のGPUで学習を回す担当者に向いています。夜の障害対応を、ツールに任せられます。",
             "1-2", "Manual recovery becomes unsustainable when jobs run on thousands of GPUs.", "数千台のGPUで学習を回すと、手作業の復旧は追いつきません。"),
        beat("注意", "最初は監視モード", "勝手に操作しない", "最初は監視モードで始まり、勝手に操作することはありません。安心して試せます。",
             "1-4", "It starts in observe-only mode by default.", "最初は監視モードで始まります。"),
        beat("確かめ方", "GitHubで公開", "ソースコード", "ソースコードはギットハブで公開されています。まずは中身を読んでみてください。",
             "1-5", "The source code is available on GitHub.", "ソースコードはGitHubで公開されています。"),
    ],
    "compare": {"beat": 0, "base": "", "subject": "", "ratio": ""},
    "ladder": {"beat": 0, "subject": "", "rows": []},
    "units": {"beat": 0, "unit": "", "subject": "", "rows": []},
    "conclusion": {"text": "まず監視モードで試す", "speech": "まずは監視モードで試すのが現実的です。"},
    "short_beats": [1, 2],
    "title": "FixtureがAI学習の自動復旧ツールFixture Recoveryを公開",
    "summary": "要約すると、FixtureがAI学習の自動復旧ツール「Fixture Recovery」を公開しました。異常の検知までの平均は69秒です。最初は監視モードで始まります。",
    "hashtags": ["#Fixture", "#AI"],
}


def good() -> dict:
    return copy.deepcopy(GOOD)


class FakeLLM:
    """llm.generate_json の偽物。台本は用意した順に返し、レビューはいつも合格。呼ばれた prompt を残す"""

    def __init__(self, scripts, clock=None, call_sec=0.0, review_ok=True):
        self.scripts = list(scripts)
        self.prompts: list[str] = []
        self.systems: list[str] = []
        self.reviews = 0
        self.clock = clock
        self.call_sec = call_sec
        self.review_ok = review_ok

    def generate_json(self, system, prompt, schema, **kw):
        if self.clock is not None:
            self.clock.t += self.call_sec
        if kw.get("purpose") == "video_review":
            self.reviews += 1
            return ({"ok": True, "issues": []} if self.review_ok else {"ok": False, "issues": ["beat2 をもっと具体的に"]}), "fake"
        self.prompts.append(prompt)
        self.systems.append(system)
        data = self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]
        return copy.deepcopy(data), "fake"


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t


@unittest.skipUnless(READY, "numpy / budoux が必要")
class ScriptSelfCorrectTest(unittest.TestCase):
    def setUp(self):
        self.pages = [fake_page()]

    def problems(self, s):
        return v3.script_problems(s, ARTICLE, self.pages)

    def run_write(self, fake, clock=None, cap=None):
        patches = [mock.patch("llm.generate_json", fake.generate_json)]
        if clock is not None:
            patches.append(mock.patch.object(v3, "time", clock))
        if cap is not None:
            patches.append(mock.patch.object(v3, "SCRIPT_TIME_CAP", cap))
        for p in patches:
            p.start()
        try:
            return v3.write_script(ARTICLE, self.pages)
        finally:
            for p in reversed(patches):
                p.stop()

    def test_fixture_script_passes(self):
        self.assertEqual(self.problems(good()), [])

    # ---- 読み上げが長すぎる ------------------------------------------------ #
    def long_script(self) -> dict:
        s = good()
        extra = ["人が起きるのを待つ必要はありません。", "担当者の負担が軽くなります。", "これが大きな違いです。"]
        for b, e in zip(s["beats"], extra * 2):
            b["speech"] = b["speech"] + e + "落ち着いて対応できます。"
        return s

    def test_shorten_speech_drops_unimportant_sentences(self):
        s = self.long_script()
        self.assertGreater(v3.speech_total(s), v3.SPEECH_TOTAL_MAX)
        self.assertTrue(any(p.startswith("読み上げの合計が長すぎ") for p in self.problems(s)))
        out = v3.shorten_speech(s)
        self.assertIsNotNone(out)
        self.assertLessEqual(v3.speech_total(out), v3.SPEECH_TOTAL_MAX)
        self.assertEqual(out["hook"], s["hook"])
        self.assertEqual(out["conclusion"], s["conclusion"])
        for old, new in zip(s["beats"], out["beats"]):
            self.assertTrue(new["speech"])
            self.assertIn(new["speech"].split("。")[0], old["speech"])  # 言葉は足さない（残った文は元の文）
            for d in v3._digits(new["keyword"] + new["sub"]):
                self.assertIn(d, new["speech"])  # 画面の数字は読み上げに残す
        self.assertEqual(self.problems(out), [])

    def test_shorten_keeps_keyword_sentences(self):
        s = good()
        s["beats"][1]["speech"] = "夜中でも、人を待たずに動き出します。" * 6 + "異常の検知までの平均は69秒でした。"
        out = v3.shorten_speech(s, limit=v3.speech_total(s) - 40)
        self.assertIsNotNone(out)
        self.assertIn("69秒", out["beats"][1]["speech"])

    def test_shorten_gives_up_instead_of_inventing(self):
        s = good()
        for b in s["beats"]:
            b["speech"] = b["speech"].split("。")[0] + "。"  # 1文ずつ: 削れない
        self.assertIsNone(v3.shorten_speech(s, limit=50))

    # ---- 証拠の文にキーワードが無い ---------------------------------------- #
    def test_relax_keyword_to_word_in_translation(self):
        s = good()
        s["beats"][0]["keyword"] = "手作業から自動へ"  # 2026-11-03 の失敗と同じ形: 訳に無い言い回し
        s["beats"][0]["ja"] = "数千台のGPUで学習を回すと、人の手での対応は追いつきません。"
        probs = self.problems(s)
        self.assertTrue(any("キーワード「手作業から自動へ」" in p for p in probs), probs)
        fixed = v3.repair_evidence(s, ARTICLE, self.pages)
        self.assertIsNotNone(fixed)
        kw = fixed["beats"][0]["keyword"]
        self.assertNotEqual(kw, "手作業から自動へ")
        self.assertIn(kw, fixed["beats"][0]["ja"])  # 証拠の訳にある言葉
        self.assertIn(kw.lower(), gate.material_text(ARTICLE).lower())  # 記事にある言葉（作らない）
        self.assertEqual(fixed["beats"][0]["target"], s["beats"][0]["target"])  # 証拠と訳はそのまま
        self.assertFalse([p for p in self.problems(fixed) if v3.EVIDENCE_PROBLEM.search(p)])

    def test_repick_line_with_keyword_number(self):
        s = good()
        s["beats"][1]["target"] = "It starts in observe-only mode by default."  # 69 の無い行
        s["beats"][1]["block"] = "1-4"
        s["beats"][1]["ja"] = "最初は監視モードで始まります。"
        self.assertTrue(any(v3.EVIDENCE_PROBLEM.search(p) for p in self.problems(s)))
        data, probs = v3.self_correct(s, ARTICLE, self.pages)
        self.assertFalse([p for p in probs if v3.EVIDENCE_PROBLEM.search(p)], probs)
        b = data["beats"][1]
        self.assertTrue(gate.key_fact_ok(b["keyword"], b["sub"], b["target"], b["ja"]))

    def test_keyword_candidates_only_article_words(self):
        b = dict(good()["beats"][0], keyword="手作業から自動へ", ja="数千台のGPUで、存在しない魔法語の対応は追いつきません。")
        cands = v3.keyword_candidates(b, ARTICLE)
        self.assertTrue(cands)
        mat = gate.material_text(ARTICLE).lower()
        for c in cands:
            self.assertIn(c.lower(), mat)
        self.assertNotIn("魔法語", cands)

    # ---- LLM の書き直し（偽物） -------------------------------------------- #
    def test_deterministic_fix_avoids_second_llm_call(self):
        s = self.long_script()
        s["beats"][0]["keyword"] = "手作業から自動へ"
        s["beats"][0]["ja"] = "数千台のGPUで学習を回すと、人の手での対応は追いつきません。"
        fake = FakeLLM([s])
        data, model = self.run_write(fake)
        self.assertEqual(len(fake.prompts), 1)  # 書き直しを頼まずに通った
        self.assertEqual(self.problems(data), [])
        self.assertTrue(data.get("_repairs"))
        self.assertLessEqual(v3.speech_total(data), v3.SPEECH_TOTAL_MAX)

    def test_feedback_has_specific_failures_and_budget(self):
        bad = good()
        bad["beats"][2]["speech"] = "数千台のGPUで学習を回す担当者に向いています。" + "1日に500回も止まる現場で役立ちます。"  # 記事に無い数字
        bad["beats"][2]["sub"] = "1日に500回"
        fake = FakeLLM([bad, good()])
        data, _ = self.run_write(fake)
        self.assertEqual(len(fake.prompts), 2)
        self.assertIn("前回の台本の問題", fake.prompts[1])
        self.assertIn("500", fake.prompts[1])
        self.assertEqual(self.problems(data), [])

    def test_length_budget_in_feedback(self):
        s = good()
        for b in s["beats"]:
            b["speech"] = "異常の検知までの平均は69秒でした。" * 1  # 削れる文が無い長さ（1文）にしてから伸ばす
        s["beats"][1]["speech"] = "異常の検知までの平均は69秒で、夜中でも人を待たずに、保存した地点から学習を自動で再開させ、担当者を起こさずに済ませられるようになりました。" * 4
        fb = v3.feedback_for(v3.script_problems(s, ARTICLE, self.pages), s, ARTICLE, self.pages)
        self.assertIn("字数の予算", fb)
        self.assertIn(f"{v3.SPEECH_TOTAL_MAX - 30}字以内", fb)
        self.assertIn("beat2", fb)

    def test_more_retries_than_before(self):
        bad = good()
        bad["beats"][2]["sub"] = "1日に500回"
        bad["beats"][2]["speech"] = "1日に500回も止まる現場で役立ちます。夜の障害対応を任せられます。"
        fake = FakeLLM([bad] * 6 + [good()])  # 7回目で通る（前は6回で諦めていた）
        data, _ = self.run_write(fake)
        self.assertEqual(len(fake.prompts), 7)
        self.assertEqual(self.problems(data), [])

    def test_time_cap_stops_llm_calls(self):
        bad = good()
        bad["beats"][2]["sub"] = "1日に500回"
        bad["beats"][2]["speech"] = "1日に500回も止まる現場で役立ちます。夜の障害対応を任せられます。"
        clock = FakeClock()
        fake = FakeLLM([bad], clock=clock, call_sec=100.0)
        with self.assertRaises(RuntimeError) as ctx:
            self.run_write(fake, clock=clock, cap=480.0)
        # 100秒の LLM を 480秒の枠で: ふつう3回 → 残り180秒で simple を1回 → 次は間に合わないので止める
        self.assertEqual(len(fake.prompts), 4)
        self.assertLessEqual(clock.t - 1000.0, 480.0)
        self.assertIn("LLM 4回", str(ctx.exception))
        self.assertIn("500", str(ctx.exception))  # 何が直らなかったかを残す
        self.assertIn("簡単な台本", fake.systems[-1])  # 最後の1回は通りやすい simple

    def test_style_only_script_used_when_time_runs_out(self):
        s = good()
        s["hook"]["speech"] = "エーアイ学習が止まったとき、いったい誰がどうやって直しますか？"  # 長い（読みの注意だけ）
        clock = FakeClock()
        fake = FakeLLM([s], clock=clock, call_sec=200.0)
        data, _ = self.run_write(fake, clock=clock, cap=480.0)
        self.assertTrue(data.get("_notes"))
        self.assertTrue(all(p.startswith(v3.STYLE_PREFIXES) for p in data["_notes"]))


if __name__ == "__main__":
    unittest.main()
