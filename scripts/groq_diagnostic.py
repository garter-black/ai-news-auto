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


def main() -> int:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        print("[groq-diagnostic] result=not_configured")
        return 0
    request = urllib.request.Request("https://api.groq.com/openai/v1/models", headers={"Authorization": "Bearer " + key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read(1_000_000).decode("utf-8"))
            print(f"[groq-diagnostic] result=ok http=200 model_count={len(data.get('data', []))}")
    except urllib.error.HTTPError as error:
        body = error.read(100_000).decode("utf-8", "replace")
        result = classify(error.code, body, error.headers.get("content-type", ""))
        print(f"[groq-diagnostic] result={result} http={error.code}")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        print("[groq-diagnostic] result=network_or_invalid_response")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
