"""朝のまとめの号（scripts/digest_page.py）。外部通信なし。"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import digest_page as dp  # noqa: E402


def article(title: str, **extra) -> dict:
    return {"slug": "x", "title": title, "channel": extra.pop("channel", ""), "tags": extra.pop("tags", []), **extra}


class ShortHeadlineTest(unittest.TestCase):
    def test_never_cuts_down_to_company_name(self):
        # 2026/11/02 の朝まとめで「・Google DeepMind」だけになった見出し
        title = "Google DeepMind、言葉で遊べる世界を作る新AI「Genie 4」を発表 操作も自由自在"
        got = dp.short_headline(article(title, channel="Google DeepMind", tags=["Google DeepMind", "Genie 4"]))
        self.assertEqual(got, "Google DeepMind、新AI「Genie 4」を発表")

    def test_keeps_product_name_and_action(self):
        got = dp.short_headline(article("Google DeepMind、高度な器用さを実現する「Gemini Robotics 2」を発表", channel="Google DeepMind"))
        self.assertEqual(got, "Google DeepMind、「Gemini Robotics 2」を発表")

    def test_short_titles_are_kept(self):
        self.assertEqual(dp.short_headline(article("AI半導体のNVIDIAが医療向けAI発表 創薬から手術支援まで自動化を促進")), "AI半導体のNVIDIAが医療向けAI発表")
        self.assertEqual(
            dp.short_headline(article("仏AIのミストラルが新AIモデル『Mistral Large 4』を公開 サイバー防衛に強み")),
            "仏AIのミストラルが新AIモデル『Mistral Large 4』を公開",
        )

    def test_drops_trailing_clause_not_subject(self):
        got = dp.short_headline(article("ChatGPTのOpenAI、12月から利用枠を「Codex」と共通化 AI作業機能は本体へ"))
        self.assertEqual(got, "ChatGPTのOpenAI、12月から利用枠を「Codex」と共通化")

    def test_does_not_end_with_particle(self):
        got = dp.short_headline(article("生成AIサービスのアガタが、社内資料のAI読み取りやVPN接続などの新サービスを開始"))
        self.assertFalse(got.endswith("が"), got)
        self.assertIn("開始", got)

    def test_real_titles_never_become_bare_names(self):
        for path in sorted((dp.ARTICLES_DIR).glob("*.json"))[:800]:
            data = json.loads(path.read_text("utf-8"))
            got = dp.short_headline(data)
            self.assertLessEqual(len(got), dp.ITEM_MAX, data["title"])
            names = {data.get("channel"), data.get("sourceName"), *data.get("tags", [])}
            self.assertNotIn(got, names, data["title"])


class EditionTest(unittest.TestCase):
    def test_edition_and_save(self):
        when = datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc)
        data = dp.edition([article("NVIDIAが医療向けAI発表", slug="a"), article("Mistralが新モデルを公開", slug="b")], when, "今朝のAIニュース2本")
        self.assertEqual(data["id"], "20261102")
        self.assertEqual(data["date"], "2026-11-02")
        self.assertEqual([i["slug"] for i in data["items"]], ["a", "b"])
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(dp, "DIGESTS_DIR", Path(tmp)):
            path = dp.save(data)
            self.assertEqual(json.loads(path.read_text("utf-8"))["items"][0]["headline"], "NVIDIAが医療向けAI発表")

    def test_design_constant_is_read_from_site(self):
        self.assertEqual(dp.design(), "E2")
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "d.ts"
            file.write_text('export const DIGEST_DESIGN: DigestDesign = "C";\n', "utf-8")
            with mock.patch.object(dp, "DESIGN_FILE", file):
                self.assertEqual(dp.design(), "C")

    def test_ai_prompt_forbids_text_logos_people(self):
        prompt = dp.ai_prompt(["surgical robot", "virtual world", "server rack"])
        for word in ("No text", "no logos", "no people", "no faces"):
            self.assertIn(word, prompt)

    def test_publish_failure_does_not_raise(self):
        with mock.patch.dict(sys.modules, {"images": mock.Mock(kv_put=mock.Mock(side_effect=RuntimeError("x")))}):
            self.assertFalse(dp.publish({"id": "20261102", "items": []}))


if __name__ == "__main__":
    unittest.main()
