"""記事本文の取得経路（scripts/feeds.py）。直接 → 読み取りサービス（鍵 → 鍵なし）→ Claude API の web fetch。
2026-11-03: JINA_API_KEY の残高切れ（402）で OpenAI 公式ブログの本文が読めず、記事を落としていた。ネットワークは使わない。"""
import io
import os
import sys
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import feeds  # noqa: E402

URL = "https://openai.com/index/rewarding-defenders-with-bigger-bug-bounties"
LONG = "We are raising rewards in the Security Bug Bounty program. " * 40
JINA_BODY = f"Title: Rewarding defenders\nPublished Time: 2026-11-02\nMarkdown Content:\n{LONG}\n[Bug bounty](https://bugcrowd.com/openai)".encode()


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))


class FakeNet:
    """URL と鍵の有無で返事を決める fetch の差し替え。呼ばれた順に記録する"""

    def __init__(self, robots: object = b"User-agent: *\nAllow: /\nDisallow: /private/\n", key_status: int = 402):
        self.calls: list[tuple[str, bool]] = []
        self.robots = robots
        self.key_status = key_status

    def __call__(self, url, timeout=25, accept="*/*", token=""):
        self.calls.append((url, bool(token)))
        if url.endswith("/robots.txt"):
            if isinstance(self.robots, int):
                raise http_error(url, self.robots)
            return self.robots, url
        if url.startswith(feeds.JINA_READER):
            if token:
                raise http_error(url, self.key_status)
            return JINA_BODY, url
        raise http_error(url, 403)  # 記事ページの直接取得は GitHub Actions ではボット除けで 403


class Base(unittest.TestCase):
    def setUp(self):
        feeds._jina_key_dead = None
        feeds._robots_cache.clear()
        feeds._api_fetch_used = 0
        feeds._api_fetch_blocked_hosts.clear()
        self.env = patch.dict(os.environ, {"JINA_API_KEY": "test-key", "WEB_FETCH_API_DISABLE": "1"})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class JinaKeyFallbackTest(Base):
    def test_exhausted_key_falls_back_to_keyless_for_the_rest_of_the_run(self):
        net = FakeNet()
        with patch.object(feeds, "fetch", net), patch("builtins.print"):
            data, _ = feeds.fetch_jina(URL)
            feeds.fetch_jina(URL + "-2")
        self.assertIn(b"Markdown Content", data)
        jina = [c for c in net.calls if c[0].startswith(feeds.JINA_READER)]
        # 1回目: 鍵で 402 → 鍵なし。2回目: はじめから鍵なし（402 の鍵を何度も送らない）
        self.assertEqual(jina, [(feeds.JINA_READER + URL, True), (feeds.JINA_READER + URL, False), (feeds.JINA_READER + URL + "-2", False)])
        self.assertEqual(feeds._jina_key_dead, "HTTP 402")

    def test_other_errors_with_key_are_not_retried_keyless(self):
        net = FakeNet(key_status=429)
        with patch.object(feeds, "fetch", net), self.assertRaises(urllib.error.HTTPError):
            feeds.fetch_jina(URL)
        self.assertIsNone(feeds._jina_key_dead)

    def test_robots_disallow_blocks_reader(self):
        net = FakeNet()
        with patch.object(feeds, "fetch", net), self.assertRaises(RuntimeError) as ctx:
            feeds.fetch_jina("https://openai.com/private/page")
        self.assertIn("robots.txt", str(ctx.exception))
        self.assertFalse(any(c[0].startswith(feeds.JINA_READER) for c in net.calls))


class RobotsTest(Base):
    def test_longest_match_wins_like_rfc9309(self):
        rules = feeds.Robots(["User-agent: *", "Allow: /", "Disallow: /private/", "Allow: /private/ok$", "", "User-agent: other", "Disallow: /"])
        self.assertTrue(rules.can_fetch("https://a.com/index/x"))
        self.assertFalse(rules.can_fetch("https://a.com/private/page"))  # Python 3.12 の robotparser は ここを許してしまう
        self.assertTrue(rules.can_fetch("https://a.com/private/ok"))
        self.assertFalse(feeds.Robots(["User-agent: *", "Disallow: /*.pdf$"]).can_fetch("https://a.com/x/file.pdf"))
        self.assertTrue(feeds.Robots(["User-agent: googlebot", "Disallow: /"]).can_fetch("https://a.com/"))  # * の決まりが無ければ制限なし

    def test_rfc9309_status_handling(self):
        for status, expected in ((403, True), (404, True), (503, False)):
            feeds._robots_cache.clear()
            with patch.object(feeds, "fetch", FakeNet(robots=status)):
                self.assertEqual(feeds.robots_allows(URL), expected, status)

    def test_robots_is_read_once_per_host(self):
        net = FakeNet()
        with patch.object(feeds, "fetch", net):
            feeds.robots_allows(URL)
            feeds.robots_allows("https://openai.com/index/other")
        self.assertEqual(sum(c[0].endswith("/robots.txt") for c in net.calls), 1)


class FetchArticleTextTest(Base):
    def test_openai_post_is_read_through_keyless_reader(self):
        with patch.object(feeds, "fetch", FakeNet()), patch("builtins.print"):
            page = feeds.fetch_article_text(URL)
        self.assertGreaterEqual(len(page["text"]), 600)
        self.assertEqual(page["title"], "Rewarding defenders")
        self.assertNotIn("](", page["text"])
        self.assertEqual(page["links"][0]["url"], "https://bugcrowd.com/openai")

    def test_failure_message_puts_reasons_before_the_full_url(self):
        def down(url, timeout=25, accept="*/*", token=""):
            if url.endswith("/robots.txt"):
                return b"", url
            raise http_error(url, 403 if not url.startswith(feeds.JINA_READER) else 429)

        with patch.object(feeds, "fetch", down), self.assertRaises(RuntimeError) as ctx:
            feeds.fetch_article_text(URL)
        message = str(ctx.exception)
        self.assertTrue(message.endswith(URL), message)  # URL は切らない
        self.assertLess(message.index("直接 HTTP 403"), message.index(URL))
        self.assertIn("読み取り HTTP 429", message)
        self.assertIn("API WEB_FETCH_API_DISABLE=1", message)


def fake_message(result):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="server_tool_use"), types.SimpleNamespace(type="web_fetch_tool_result", content=result)],
        usage=types.SimpleNamespace(input_tokens=10000, output_tokens=50, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


class FakeLlm(types.ModuleType):
    def __init__(self, message=None, choose=("claude-haiku-5-5", "")):
        super().__init__("llm")
        self.message = message
        self.choose = choose
        self.requests: list[dict] = []
        self.usage: list[tuple] = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.requests.append(kwargs)
                return outer.message

        self._client = types.SimpleNamespace(messages=Messages())

    def api_choose(self, purpose):
        return self.choose

    def _get_api_client(self):
        return self._client

    def record_api_usage(self, purpose, model, usage):
        self.usage.append((purpose, model))
        return 0.001

    def _api_error_kind(self, error):
        return "transient"

    def mark_api_exhausted(self, detail):
        return ""


class ApiWebFetchTest(Base):
    def setUp(self):
        super().setUp()
        os.environ.pop("WEB_FETCH_API_DISABLE", None)

    def run_api(self, llm):
        with patch.dict(sys.modules, {"llm": llm}), patch.object(feeds, "fetch", FakeNet()):
            return feeds._fetch_via_api(URL)

    def test_uses_fetched_document_text_and_records_budget(self):
        document = types.SimpleNamespace(title="Rewarding defenders", source=types.SimpleNamespace(type="text", data=LONG))
        llm = FakeLlm(fake_message(types.SimpleNamespace(type="web_fetch_result", url=URL, content=document)))
        page = self.run_api(llm)
        self.assertEqual(page["title"], "Rewarding defenders")
        self.assertGreaterEqual(len(page["text"]), 600)
        self.assertEqual(llm.usage, [("helper", "claude-haiku-5-5")])
        tool = llm.requests[0]["tools"][0]
        self.assertEqual((tool["type"], tool["max_uses"], tool["allowed_domains"]), (feeds.API_FETCH_TOOL, 1, ["openai.com"]))

    def test_inaccessible_host_is_not_retried_in_the_same_run(self):
        llm = FakeLlm(fake_message(types.SimpleNamespace(type="web_fetch_tool_result_error", error_code="url_not_accessible")))
        with self.assertRaises(RuntimeError):
            self.run_api(llm)
        with self.assertRaises(RuntimeError) as ctx:
            self.run_api(llm)
        self.assertIn("この実行", str(ctx.exception))
        self.assertEqual(len(llm.requests), 1)

    def test_budget_block_skips_the_call(self):
        llm = FakeLlm(choose=(None, "今月の予算 $95 に到達"))
        with self.assertRaises(RuntimeError) as ctx:
            self.run_api(llm)
        self.assertIn("予算", str(ctx.exception))
        self.assertEqual(llm.requests, [])


if __name__ == "__main__":
    unittest.main()
