"""Read-only identity audit: failures, known legacy warnings, and safe requests."""
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_search_identity as identity

BASE, OLD = "https://site.test", "https://old.test"


class FakeFetcher:
    def __init__(self, values):
        self.values, self.calls = values, []

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self.values.get(url, identity.Response(url, 404))


class IdentityTest(unittest.TestCase):
    def fixture(self, *, robots_meta="index, follow", news=True, publisher_url=BASE):
        publisher = {"@type": "NewsMediaOrganization", "@id": BASE + "/#publisher", "name": "サイソクニュース",
                     "alternateName": ["サイソクAIニュース", "サイソク"],
                     "url": publisher_url, "logo": {"url": BASE + "/icons/icon-512.png"}}
        website = {"@type": "WebSite", "name": "サイソクニュース", "url": BASE, "publisher": publisher}
        html = (f'<link rel="canonical" href="{BASE}/"><meta name="robots" content="{robots_meta}">'
                '<link rel="icon" href="/favicon.ico?v=1"><link rel="icon" href="/icons/icon-512.png">'
                '<a href="/articles/sample">Article</a>'
                '<script src="https://ads.example/ad.js"></script><img src="https://ads.example/pixel">'
                '<a href="https://ads.example/click">Ad</a>'
                f'<script type="application/ld+json">{json.dumps(website)}</script>')
        item = ('<url><loc>' + BASE + '/articles/sample</loc><news:news><news:publication_date>'
                '2026-10-31T00:00:00Z</news:publication_date></news:news></url>') if news else ''
        xml = ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
               'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">' + item + '</urlset>')
        return FakeFetcher({
            BASE + "/": identity.Response(BASE + "/", 200, {"content-type": "text/html"}, html.encode()),
            BASE + "/icons/icon-512.png": identity.Response(BASE + "/icons/icon-512.png", 200,
                {"content-type": "image/png"}, b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 512, 512)),
            BASE + "/icon": identity.Response(BASE + "/icon", 200,
                {"content-type": "image/png"}, b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 512, 512)),
            BASE + "/favicon.ico?v=1": identity.Response(BASE + "/favicon.ico?v=1", 200, {"content-type": "image/vnd.microsoft.icon"}),
            BASE + "/robots.txt": identity.Response(BASE + "/robots.txt", 200, body=b"User-agent: *\nAllow: /\nDisallow: /api/"),
            BASE + "/news-sitemap.xml": identity.Response(BASE + "/news-sitemap.xml", 200, body=xml.encode()),
        })

    def run_audit(self, fetcher, **kwargs):
        return identity.audit(BASE, OLD, fetcher=fetcher, **kwargs)

    def test_legacy_404_is_warning_and_no_ad_requests(self):
        fetcher = self.fixture()
        result = self.run_audit(fetcher)
        self.assertEqual((result["failures"], result["warnings"]), (0, 3))
        self.assertTrue(all(url.startswith((BASE + "/", OLD + "/")) for url in fetcher.calls))
        self.assertEqual(len(fetcher.calls), 9)
        news = next(row for row in result["checks"] if row["check"] == "news_sitemap_parse")
        self.assertEqual(news["details"]["news_count"], 1)

    def test_permanent_redirected_legacy_is_pass(self):
        fetcher = self.fixture()
        for path in ["/", "/about", "/articles/sample"]:
            fetcher.values[OLD + path] = identity.Response(BASE + path, 200,
                redirects=[{"url": OLD + path, "status": 308, "target": BASE + path}])
        self.assertEqual(self.run_audit(fetcher)["status"], "pass")

    def test_retirement_is_explicit_warning_not_success(self):
        result = self.run_audit(self.fixture(), legacy_retired=True)
        legacy = [row for row in result["checks"] if row["check"] == "legacy_redirect"]
        self.assertTrue(all(row["status"] == "warning" and row["details"]["state"] == "retired_host" for row in legacy))

    def test_current_site_noindex_is_failure(self):
        for value in ["noindex, follow", "NOINDEX nofollow", "none"]:
            with self.subTest(value=value):
                result = self.run_audit(self.fixture(robots_meta=value))
                self.assertEqual(result["status"], "fail")
        fetcher = self.fixture()
        fetcher.values[BASE + "/"].headers["x-robots-tag"] = "googlebot: noindex"
        self.assertEqual(self.run_audit(fetcher)["status"], "fail")

    def test_wrong_logo_dimensions_or_mime_fail(self):
        for mime, dimensions in [("image/png", (600, 60)), ("text/html", (512, 512))]:
            with self.subTest(mime=mime, dimensions=dimensions):
                fetcher = self.fixture()
                logo = fetcher.values[BASE + "/icons/icon-512.png"]
                logo.headers["content-type"] = mime
                logo.body = logo.body[:16] + struct.pack(">II", *dimensions)
                self.assertEqual(self.run_audit(fetcher)["status"], "fail")

    def test_malformed_publisher_url_is_failure_not_crash(self):
        self.assertEqual(self.run_audit(self.fixture(publisher_url=None))["status"], "fail")

    def test_robots_disallow_or_broken_favicon_fail(self):
        fetcher = self.fixture()
        fetcher.values[BASE + "/robots.txt"].body = b"User-agent: *\nDisallow: /"
        self.assertEqual(self.run_audit(fetcher)["status"], "fail")
        fetcher = self.fixture()
        del fetcher.values[BASE + "/favicon.ico?v=1"]
        self.assertEqual(self.run_audit(fetcher)["status"], "fail")

    def test_empty_news_is_warning_not_broken_site(self):
        result = self.run_audit(self.fixture(news=False))
        self.assertEqual(result["failures"], 0)
        self.assertEqual(result["warnings"], 4)

    def test_stable_icon_link_is_required(self):
        fetcher = self.fixture()
        fetcher.values[BASE + "/"].body = fetcher.values[BASE + "/"].body.replace(
            b'<link rel="icon" href="/icons/icon-512.png">', b'')
        result = self.run_audit(fetcher)
        check = next(row for row in result["checks"] if row["check"] == "favicon_stable_link")
        self.assertEqual(check["status"], "fail")

    def test_same_size_different_icon_bytes_are_detected(self):
        fetcher = self.fixture()
        fetcher.values[BASE + "/icon"].body += b'different pixels'
        result = self.run_audit(fetcher)
        check = next(row for row in result["checks"] if row["check"] == "favicon_legacy_route_matches")
        self.assertEqual(check["status"], "fail")

    def test_legacy_icon_route_must_remain_available(self):
        fetcher = self.fixture()
        del fetcher.values[BASE + "/icon"]
        result = self.run_audit(fetcher)
        check = next(row for row in result["checks"] if row["check"] == "favicon_legacy_route_matches")
        self.assertEqual(check["status"], "fail")

    def test_external_article_argument_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_audit(self.fixture(), article_path="https://ads.example/articles/a")

    def test_external_redirect_stops_before_request(self):
        class Redirect:
            status, headers = 302, {"Location": "https://ads.example/pixel"}
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class Opener:
            def __init__(self): self.requests = []
            def open(self, req, **kwargs):
                self.requests.append(req)
                return Redirect()
        fetcher = identity.Fetcher([BASE, OLD])
        fetcher.opener = Opener()
        result = fetcher.get(BASE + "/favicon.ico", image=True)
        self.assertEqual(result.error, "host_not_allowed")
        self.assertEqual(len(fetcher.opener.requests), 1)
        self.assertEqual(fetcher.opener.requests[0].get_header("User-agent"), identity.USER_AGENT)
        self.assertNotIn("Googlebot", identity.USER_AGENT)

    def test_full_icon_comparison_has_a_size_limit(self):
        class LargeImage:
            status, headers = 200, {"Content-Type": "image/png"}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'x' * size
        class Opener:
            def open(self, req, **kwargs): return LargeImage()
        fetcher = identity.Fetcher([BASE, OLD])
        fetcher.opener = Opener()
        result = fetcher.get(BASE + "/icon", image=True, full_image=True)
        self.assertEqual(result.error, "body_too_large")
        self.assertEqual(result.body, b'')


if __name__ == "__main__":
    unittest.main()
