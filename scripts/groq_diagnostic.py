#!/usr/bin/env python3
"""Read-only Groq model-list probe. Never print the key or provider response body."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def classify(status: int, body: str, content_type: str = "") -> str:
    lower = body.lower()
    if status == 200:
        return "ok"
    if status == 401 or any(s in lower for s in ("invalid api key", "invalid_api_key", "api key has been revoked")):
        return "authentication"
    if any(s in lower for s in ("unsupported country", "unsupported_country", "country is not supported", "region is not supported", "not available in your region")):
        return "region_restriction"
    if any(s in lower for s in ("organization has been disabled", "organization is disabled", "organization is suspended", "account is suspended", "organization_deactivated")):
        return "organization_disabled"
    if any(s in lower for s in ("cloudflare", "challenge-platform", "just a moment")) and "html" in content_type.lower():
        return "security_challenge"
    if status == 403:
        return "permission_or_policy"
    if status == 429:
        return "rate_or_quota"
    return "other"


def hints(body: str) -> list[str]:
    # Only publish a fixed vocabulary; never echo any part of an API response.
    low = body.lower()
    known = {
        "organization": ("organization", "organisation"),
        "api_key": ("api key", "api_key", "apikey"),
        "region": ("country", "region", "location", "geograph"),
        "blocked": ("blocked", "denied", "forbidden", "restrict"),
        "disabled": ("disabled", "deactivated", "suspended", "revoked"),
        "verification": ("verification", "verify", "verified"),
        "terms": ("terms of service", "terms and conditions"),
        "ip_address": ("ip address", "ip_address", "ip-address"),
        "cloudflare": ("cloudflare", "cf-ray"),
        "billing": ("billing", "payment", "credit"),
        "permission": ("permission", "access"),
    }
    return [name for name, words in known.items() if any(word in low for word in words)]


def probe(label: str, key: str = "", identify: bool = False) -> None:
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    if identify:
        headers["User-Agent"] = "saisoku-ai-news/1.0 (+https://ai-tech-express.jp)"
    request = urllib.request.Request("https://api.groq.com/openai/v1/models", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read(1_000_000).decode("utf-8"))
            print(f"[groq-diagnostic] probe={label} result=ok http=200 model_count={len(data.get('data', []))}")
    except urllib.error.HTTPError as error:
        body = error.read(100_000).decode("utf-8", "replace")
        result = classify(error.code, body, error.headers.get("content-type", ""))
        kind = "json" if "json" in error.headers.get("content-type", "").lower() else "html" if "html" in error.headers.get("content-type", "").lower() else "other"
        print(f"[groq-diagnostic] probe={label} result={result} http={error.code} body_type={kind} hints={','.join(hints(body)) or 'none'}")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        print(f"[groq-diagnostic] probe={label} result=network_or_invalid_response")


def main() -> int:
    key = os.environ.get("GROQ_API_KEY", "")
    probe("anonymous")
    probe("anonymous_identified", identify=True)
    if key:
        probe("configured_identified", key, identify=True)
        # Exercise the exact production model-list path; this is a read-only GET.
        try:
            import llm
            print(f"[groq-diagnostic] probe=production_code model_count={len(llm._groq_models())}")
        except Exception:
            print("[groq-diagnostic] probe=production_code result=error")
    else:
        print("[groq-diagnostic] probe=configured result=not_configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
