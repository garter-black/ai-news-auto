#!/usr/bin/env python3
"""Read-only search identity check; never executes scripts or follows ad links.

python3 scripts/check_search_identity.py --json
Exit 1 means the current site failed a check. Legacy-host warnings exit 0.
--legacy-retired labels an intentional retirement; it does not claim migration succeeded.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import hashlib
import json
import re
import struct
import sys
from urllib import error, parse, request, robotparser
import xml.etree.ElementTree as ET

USER_AGENT = "SaisokuSearchIdentityAudit/1.0 (+https://ai-tech-express.jp/about; read-only)"
MAX_BODY = 2_000_000
# 2026-11-02 に改名。旧名は alternateName に残っていること（Google が同じ媒体とつなげるため）
SITE_NAME = "サイソクニュース"
FORMER_NAME = "サイソクAIニュース"
MAX_IMAGE = 1_000_000


def base_url(value: str) -> str:
    parts = parse.urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.path not in {"", "/"}
            or parts.query or parts.fragment):
        raise ValueError("base must be an http(s) origin without credentials, path, query, or fragment")
    return value.rstrip("/")


@dataclass
class Response:
    url: str
    status: int = 0
    headers: dict = field(default_factory=dict)
    body: bytes = b""
    redirects: list = field(default_factory=list)
    error: str | None = None


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self, bases: list[str], timeout: float = 15):
        self.hosts = {parse.urlsplit(url).netloc.lower() for url in bases}
        self.timeout = timeout
        self.opener = request.build_opener(NoRedirect)

    def allowed(self, url: str) -> bool:
        parts = parse.urlsplit(url)
        return (parts.scheme in {"http", "https"} and not parts.username
                and not parts.password and parts.netloc.lower() in self.hosts)

    def get(self, url: str, *, image: bool = False, full_image: bool = False) -> Response:
        redirects = []
        for _ in range(6):
            if not self.allowed(url):
                return Response(url, redirects=redirects, error="host_not_allowed")
            try:
                req = request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
                try:
                    response = self.opener.open(req, timeout=self.timeout)
                except error.HTTPError as exc:
                    response = exc
                with response:
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    if response.status in {301, 302, 303, 307, 308}:
                        location = headers.get("location")
                        if not location:
                            return Response(url, response.status, headers, redirects=redirects, error="redirect_without_location")
                        target = parse.urljoin(url, location)
                        redirects.append({"url": url, "status": response.status, "target": target})
                        url = target
                        continue
                    limit = MAX_IMAGE if image else MAX_BODY
                    body = response.read(32 if image and not full_image else limit + 1)
                    if (not image or full_image) and len(body) > limit:
                        return Response(url, response.status, headers, redirects=redirects, error="body_too_large")
                    return Response(url, response.status, headers, body, redirects)
            except (OSError, ValueError, error.URLError) as exc:
                return Response(url, redirects=redirects, error=type(exc).__name__)
        return Response(url, redirects=redirects, error="too_many_redirects")


class HomeHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals, self.icons, self.robots, self.articles, self.jsonld = [], [], [], [], []
        self.in_jsonld = False
        self.buffer = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        rel = attrs.get("rel", "").lower().split()
        if tag == "link" and "canonical" in rel:
            self.canonicals.append(attrs.get("href", ""))
        if tag == "link" and ("icon" in rel or "apple-touch-icon" in rel):
            self.icons.append(attrs.get("href", ""))
        if tag == "meta" and attrs.get("name", "").lower() in {"robots", "googlebot"}:
            self.robots.append(attrs.get("content", ""))
        if tag == "a" and parse.urlsplit(attrs.get("href", "")).path.startswith("/articles/"):
            self.articles.append(attrs.get("href", ""))
        if tag == "script" and attrs.get("type", "").lower() == "application/ld+json":
            self.in_jsonld, self.buffer = True, ""

    def handle_data(self, data):
        if self.in_jsonld:
            self.buffer += data

    def handle_endtag(self, tag):
        if tag == "script" and self.in_jsonld:
            self.jsonld.append(self.buffer)
            self.in_jsonld = False


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def has_type(node, name):
    value = node.get("@type", [])
    return name in (value if isinstance(value, list) else [value])


def noindex(value: str) -> bool:
    return any(token.lower() in {"noindex", "none"} for token in re.split(r"[\s,:]+", value))


def same_base(value, base):
    return isinstance(value, str) and value.rstrip("/") == base


def png_size(body: bytes):
    if len(body) >= 24 and body[:8] == b"\x89PNG\r\n\x1a\n" and body[12:16] == b"IHDR":
        return list(struct.unpack(">II", body[16:24]))
    return None


def audit(base, legacy_base, *, fetcher=None, legacy_retired=False, article_path=None):
    base, legacy_base = base_url(base), base_url(legacy_base)
    fetcher = fetcher or Fetcher([base, legacy_base])
    checks, requests = [], []

    def check(name, passed, details=None, *, warning=False):
        checks.append({"check": name, "status": "pass" if passed else "warning" if warning else "fail", "details": details})

    def get(url, *, image=False, full_image=False):
        result = fetcher.get(url, image=image, full_image=full_image)
        requests.append({"requested_url": url, "final_url": result.url, "status": result.status,
                         "redirects": result.redirects, "error": result.error})
        return result

    home = get(base + "/")
    check("home_http", home.status == 200 and not home.error, home.status)
    html = HomeHTML()
    html.feed(home.body.decode("utf-8", "replace"))
    canonical = [parse.urljoin(base + "/", value) for value in html.canonicals]
    check("home_canonical", len(canonical) == 1 and canonical[0].rstrip("/") == base, canonical)
    robot_values = html.robots + [home.headers.get("x-robots-tag", "")]
    check("home_indexable", not any(noindex(value) for value in robot_values), robot_values)

    structured = []
    try:
        for block in html.jsonld:
            structured.extend(nodes(json.loads(block)))
        check("jsonld_parse", bool(structured))
    except (ValueError, TypeError):
        check("jsonld_parse", False, "invalid JSON-LD")
    websites = [node for node in structured if has_type(node, "WebSite")]
    check("website", len(websites) == 1 and websites[0].get("name") == SITE_NAME
          and same_base(websites[0].get("url"), base), {"count": len(websites)})
    publisher = websites[0].get("publisher", {}) if websites else {}
    publisher = publisher if isinstance(publisher, dict) else {}
    if set(publisher) == {"@id"}:
        publisher = next((node for node in structured if node.get("@id") == publisher["@id"] and "name" in node), publisher)
    check("publisher_identity", has_type(publisher, "NewsMediaOrganization")
          and publisher.get("@id") == base + "/#publisher"
          and publisher.get("name") == SITE_NAME
          and FORMER_NAME in (publisher.get("alternateName") or [])
          and same_base(publisher.get("url"), base),
          {key: publisher.get(key) for key in ["@type", "@id", "name", "alternateName", "url"]})
    logo = publisher.get("logo", {})
    logo_url = logo.get("url", logo.get("contentUrl", "")) if isinstance(logo, dict) else logo
    logo_url = parse.urljoin(base + "/", logo_url) if isinstance(logo_url, str) and logo_url else ""
    logo_response = None
    if logo_url:
        image = logo_response = get(logo_url, image=True, full_image=True)
        dimensions = png_size(image.body)
        check("publisher_logo", image.status == 200 and not image.error and dimensions == [512, 512]
              and image.headers.get("content-type", "").split(";")[0] == "image/png"
              and not noindex(image.headers.get("x-robots-tag", "")),
              {"url": logo_url, "dimensions": dimensions, "status": image.status})
    else:
        check("publisher_logo", False, "missing logo URL")

    icons = list(dict.fromkeys(parse.urljoin(base + "/", href) for href in html.icons if href))
    check("favicon_links", bool(icons), icons)
    stable_icon_url = base + "/icons/icon-512.png"
    check("favicon_stable_link", stable_icon_url in icons, stable_icon_url)
    for icon_url in icons:
        icon = logo_response if icon_url == logo_url else get(icon_url, image=True)
        check("favicon_reachable", icon.status == 200 and not icon.error
              and icon.headers.get("content-type", "").lower().startswith("image/"),
              {"url": icon_url, "status": icon.status})

    # Full bytes only for these two first-party assets (bounded to 1 MB); no image library.
    stable_icon = logo_response if logo_url == stable_icon_url else get(stable_icon_url, image=True, full_image=True)
    legacy_icon = get(base + "/icon", image=True, full_image=True)
    same_icon = (stable_icon.status == legacy_icon.status == 200 and not stable_icon.error
                 and not legacy_icon.error and png_size(stable_icon.body) == [512, 512]
                 and stable_icon.body == legacy_icon.body)
    check("favicon_legacy_route_matches", same_icon,
          {"stable_url": stable_icon_url, "legacy_url": base + "/icon",
           "stable_sha256": hashlib.sha256(stable_icon.body).hexdigest(),
           "legacy_sha256": hashlib.sha256(legacy_icon.body).hexdigest()})

    robots = get(base + "/robots.txt")
    check("robots_http", robots.status == 200 and not robots.error, robots.status)
    if robots.status == 200 and not robots.error:
        rules = robotparser.RobotFileParser()
        rules.parse(robots.body.decode("utf-8", "replace").splitlines())
        # These names evaluate robots rules only; every HTTP request uses the honest audit UA.
        check("robots_allows_home", rules.can_fetch("Googlebot", base + "/"))
        for image_url in icons + ([logo_url] if logo_url else []):
            if parse.urlsplit(image_url).netloc == parse.urlsplit(base).netloc:
                check("robots_allows_image", rules.can_fetch("Googlebot-Image", image_url), image_url)

    sitemap = get(base + "/news-sitemap.xml")
    check("news_sitemap_http", sitemap.status == 200 and not sitemap.error, sitemap.status)
    try:
        tree = ET.fromstring(sitemap.body)
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "n": "http://www.google.com/schemas/sitemap-news/0.9"}
        entries = tree.findall("s:url/n:news", ns)
        dates = sorted(node.findtext("n:publication_date", default="", namespaces=ns) for node in entries)
        check("news_sitemap_parse", tree.tag == "{" + ns["s"] + "}urlset",
              {"news_count": len(entries), "oldest_publication": dates[0] if dates else None,
               "newest_publication": dates[-1] if dates else None})
        check("news_sitemap_nonempty", bool(entries), "Zero entries can be valid when no eligible news was published recently", warning=True)
    except ET.ParseError:
        check("news_sitemap_parse", False, "invalid XML")

    if article_path is None:
        article_path = next((parse.urlsplit(href).path for href in html.articles
                             if parse.urlsplit(parse.urljoin(base + "/", href)).netloc == parse.urlsplit(base).netloc), None)
    if article_path is not None and (not article_path.startswith("/articles/") or parse.urlsplit(article_path).netloc
                                     or parse.urlsplit(article_path).query or parse.urlsplit(article_path).fragment):
        raise ValueError("article-path must be a local /articles/<slug> path")
    paths = ["/", "/about"] + ([article_path] if article_path else [])
    if not article_path:
        check("legacy_article_sample", False, "No article link found on home page", warning=True)
    for path in paths:
        result = get(legacy_base + path)
        migrated = (result.status == 200 and not result.error and result.url == base + path
                    and bool(result.redirects) and all(hop["status"] in {301, 308} for hop in result.redirects))
        check("legacy_redirect", migrated,
              {"path": path, "status": result.status, "final_url": result.url,
               "state": "redirected" if migrated else "retired_host" if legacy_retired else "migration_unresolved"}, warning=True)

    failures = sum(item["status"] == "fail" for item in checks)
    warnings = sum(item["status"] == "warning" for item in checks)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "base": base, "legacy_base": legacy_base,
            "legacy_retired": legacy_retired, "user_agent": USER_AGENT,
            "scope": "Direct HTML, robots, sitemap, image headers, and bounded icon bytes only; no JavaScript, ads, posting, or indexing requests",
            "status": "fail" if failures else "warning" if warnings else "pass",
            "failures": failures, "warnings": warnings, "checks": checks, "requests": requests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="https://ai-tech-express.jp")
    parser.add_argument("--legacy-base", default="https://ai-news-auto.vercel.app")
    parser.add_argument("--legacy-retired", action="store_true", help="Label legacy warnings as intentional retirement, not a completed migration")
    parser.add_argument("--article-path", help="Existing local /articles/<slug> sample; default: first article link on home page")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report")
    args = parser.parse_args()
    try:
        report = audit(args.base, args.legacy_base, legacy_retired=args.legacy_retired, article_path=args.article_path)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            print(f'{item["status"]:7} {item["check"]}: {json.dumps(item["details"], ensure_ascii=False)}')
        print(f'{report["status"]}: {report["failures"]} failures, {report["warnings"]} warnings')
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
