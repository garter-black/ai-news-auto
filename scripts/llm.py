#!/usr/bin/env python3
"""無料枠のモデルを順番に使い回して JSON を生成するヘルパー。

  from llm import generate_json
  data, model = generate_json(system_text, prompt, schema, tier="bulk")

- tier="quality": Claude（使えるとき）→ main.py と同じ Gemini Flash 系
- tier="bulk"   : 無料枠の大きい軽量モデル（flash-lite → Gemma）→ quality → 外部無料API（キーがあれば）
                  → 最後の予備として Claude（1日 CLAUDE_DAILY_MAX 回まで）→ Codex（ローカルのみ）
- Claude は Claude Code CLI（`claude -p`）をサブスクリプションの枠で呼ぶ。使えるのは
  `claude` が PATH にあり、かつ CLAUDE_CODE_OAUTH_TOKEN（GitHub Actions。`claude setup-token` で発行）
  または LLM_USE_LOCAL_CLAUDE=1（手元の Mac）のときだけ。CLAUDE_MODEL でモデルを変更（既定 sonnet）。
- Codex は `codex` が PATH にあり LLM_USE_LOCAL_CODEX=1 のときだけ（手元の ChatGPT ログイン用。CI では使わない）。
- 429 / RESOURCE_EXHAUSTED のモデルはその実行中は使わない。404 は飛ばす。5xx は短く2回まで再試行。
- モデルごとの1日の使用回数を content/llm_usage.json に記録（直近7日分）。

  python3 scripts/llm.py            # 使えるモデル一覧と今日の使用回数を表示
  python3 scripts/llm.py --ping     # bulk で小さな JSON を1回生成して動作確認
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    # Gemini の無料枠は太平洋時間の 0 時にリセットされる
    QUOTA_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001 - tzdata が無い環境
    QUOTA_TZ = None

ROOT = Path(__file__).resolve().parent.parent
USAGE_PATH = ROOT / "content" / "llm_usage.json"
REQUEST_TIMEOUT_MS = 240_000
TRANSIENT_RETRIES = 2
MAX_SHORT_WAIT = 20  # 秒。これより長い待機指示（＝日次上限）は待たずに次のモデルへ

# 候補（先頭ほど優先）。models.list() に実在するものだけ使う。
QUALITY_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-flash-latest",
    # 無料枠はモデルごとに別枠なので、新しい Flash も予備として足す
    "gemini-3.7-flash",
    "gemini-3.8-flash",
]
BULK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]
GROQ_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen/qwen3-32b"]
GROQ_USER_AGENT = "saisoku-ai-news/1.0 (+https://ai-tech-express.jp)"

CLAUDE_DEFAULT_MODEL = "sonnet"
CLAUDE_DAILY_MAX_DEFAULT = 40  # bulk での1日の上限（オーナーの対話利用と枠を共有するため）
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "600"))
CODEX_TIMEOUT = int(os.environ.get("CODEX_TIMEOUT", "600"))

_client = None
_available: set[str] | None = None
_exhausted: set[str] = set()


def log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------- #
# 使用回数の記録
# --------------------------------------------------------------------------- #


def _today() -> str:
    now = datetime.now(QUOTA_TZ) if QUOTA_TZ else datetime.utcnow() - timedelta(hours=8)
    return now.strftime("%Y-%m-%d")


def _load_usage() -> dict:
    try:
        return json.loads(USAGE_PATH.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _record_usage(model: str) -> None:
    usage = _load_usage()
    day = usage.setdefault(_today(), {})
    day[model] = day.get(model, 0) + 1
    keep = sorted(usage)[-7:]
    usage = {d: usage[d] for d in keep}
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_PATH.write_text(json.dumps(usage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")


def usage_today() -> dict:
    """{model: 今日（太平洋時間）の呼び出し回数}"""
    return dict(_load_usage().get(_today(), {}))


# --------------------------------------------------------------------------- #
# モデル一覧
# --------------------------------------------------------------------------- #


def _gemini_client():
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY が未設定です")
        from google import genai
        from google.genai import types

        _client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS))
    return _client


def available_models() -> set[str]:
    """generateContent に対応する Gemini API のモデル名（1回だけ取得してキャッシュ）"""
    global _available
    if _available is None:
        try:
            names = set()
            for model in _gemini_client().models.list():
                actions = getattr(model, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    names.add(str(model.name).replace("models/", ""))
            _available = names
        except Exception as error:  # noqa: BLE001 - 一覧が取れなくても候補をそのまま試す
            log(f"[llm] models.list() に失敗（候補をそのまま使います）: {str(error)[:120]}")
            _available = set(QUALITY_MODELS + BULK_MODELS)
    return _available


def _http_json(url: str, payload: dict | None, headers: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _groq_models() -> list[str]:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return []
    wanted = [m.strip() for m in os.environ.get("GROQ_MODELS", "").split(",") if m.strip()] or GROQ_MODELS
    try:
        listed = {m["id"] for m in _http_json("https://api.groq.com/openai/v1/models", None, {"Authorization": f"Bearer {key}", "User-Agent": GROQ_USER_AGENT}, 30).get("data", [])}
        wanted = [m for m in wanted if m in listed]
    except Exception as error:  # noqa: BLE001
        log(f"[llm] Groq のモデル一覧取得に失敗: {str(error)[:100]}")
    return [f"groq:{m}" for m in wanted]


# Cloudflare Workers AI（無料枠: 1日1万ニューロン）。OpenAI 互換の窓口を使う
CLOUDFLARE_MODELS = ["@cf/openai/gpt-oss-120b", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"]


def _cloudflare_models() -> list[str]:
    if not (os.environ.get("CLOUDFLARE_API_TOKEN") and os.environ.get("CLOUDFLARE_ACCOUNT_ID")):
        return []
    wanted = [m.strip() for m in os.environ.get("CLOUDFLARE_MODELS", "").split(",") if m.strip()] or CLOUDFLARE_MODELS
    return [f"cloudflare:{m}" for m in wanted]


def _openrouter_models() -> list[str]:
    if not os.environ.get("OPENROUTER_API_KEY"):
        return []
    wanted = [m.strip() for m in os.environ.get("OPENROUTER_MODELS", "").split(",") if m.strip()]
    if not wanted:
        try:
            data = _http_json("https://openrouter.ai/api/v1/models", None, {}, 30).get("data", [])
            free = [m for m in data if str(m.get("id", "")).endswith(":free") and (m.get("context_length") or 0) >= 32000]
            free.sort(key=lambda m: m.get("context_length") or 0, reverse=True)
            wanted = [m["id"] for m in free[:3]]
        except Exception as error:  # noqa: BLE001
            log(f"[llm] OpenRouter のモデル一覧取得に失敗: {str(error)[:100]}")
    return [f"openrouter:{m}" for m in wanted if m.endswith(":free")]


def claude_enabled() -> bool:
    """claude CLI があり、CI ではトークン、手元では LLM_USE_LOCAL_CLAUDE=1 があるときだけ使う"""
    if not shutil.which("claude"):
        return False
    return bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")) or os.environ.get("LLM_USE_LOCAL_CLAUDE") == "1"


def _claude_models() -> list[str]:
    return [f"claude:{os.environ.get('CLAUDE_MODEL') or CLAUDE_DEFAULT_MODEL}"] if claude_enabled() else []


def _codex_models() -> list[str]:
    # ChatGPT ログインは手元の Mac だけ。CI（GITHUB_ACTIONS）では絶対に使わない
    if os.environ.get("LLM_USE_LOCAL_CODEX") != "1" or os.environ.get("CI") or not shutil.which("codex"):
        return []
    return [f"codex:{os.environ.get('CODEX_MODEL') or 'default'}"]


def claude_calls_today() -> int:
    return sum(n for m, n in usage_today().items() if m.startswith("claude:"))


def _claude_daily_max() -> int:
    try:
        return int(os.environ.get("CLAUDE_DAILY_MAX", CLAUDE_DAILY_MAX_DEFAULT))
    except ValueError:
        return CLAUDE_DAILY_MAX_DEFAULT


_tiers: dict[str, list[str]] = {}


def models_for(tier: str) -> list[str]:
    if tier not in _tiers:
        have = available_models()
        override = [os.environ["GEMINI_MODEL"]] if os.environ.get("GEMINI_MODEL") else []
        quality = [m for m in dict.fromkeys(override + QUALITY_MODELS) if m in have]
        claude = _claude_models()
        if tier == "quality":
            chosen = claude + quality
        else:
            bulk = [m for m in BULK_MODELS if m in have]
            # Claude は枠をオーナーと共有するので bulk では最後の予備（1日 CLAUDE_DAILY_MAX 回まで）
            chosen = list(dict.fromkeys(bulk + quality)) + _groq_models() + _openrouter_models() + _cloudflare_models() + claude + _codex_models()
        _tiers[tier] = chosen
        log(f"[llm] {tier}: {' → '.join(chosen) or '（なし）'}")
    return _tiers[tier]


# --------------------------------------------------------------------------- #
# JSON の取り出しと検証
# --------------------------------------------------------------------------- #


def parse_json_lenient(text: str) -> dict:
    """``` で囲まれていても、前後に説明文があっても、いちばん外側の {...} を取り出す。"""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("JSON が見つかりません")
        chunk = text[start : end + 1]
        try:
            value = json.loads(chunk)
        except json.JSONDecodeError:
            # 末尾カンマなど軽い崩れを直して再挑戦
            value = json.loads(re.sub(r",\s*([}\]])", r"\1", chunk), strict=False)
    if not isinstance(value, dict):
        raise ValueError("JSON がオブジェクトではありません")
    return value


def missing_keys(data: dict, schema: dict) -> list[str]:
    return [k for k in schema.get("required", []) if k not in data or data[k] is None]


def _skeleton(schema: dict):
    """スキーマから「この形の JSON を返して」と示す見本を作る（Gemma・外部API向け）"""
    kind = str(schema.get("type", "")).upper()
    if kind == "OBJECT":
        props = schema.get("properties", {})
        order = schema.get("propertyOrdering") or list(props)
        return {k: _skeleton(props[k]) for k in order if k in props}
    if kind == "ARRAY":
        return [_skeleton(schema.get("items", {"type": "STRING"}))]
    if "enum" in schema:
        return "|".join(schema["enum"])
    return {"STRING": "...", "BOOLEAN": True, "INTEGER": 0, "NUMBER": 0}.get(kind, "...")


def _plain_prompt(system_instruction: str, prompt: str, schema: dict) -> str:
    return (
        f"# 役割と規則\n{system_instruction}\n\n# 依頼\n{prompt}\n\n"
        "# 出力形式\n説明や ``` は付けず、次の形の JSON オブジェクトだけを出力する。"
        f"必須キー: {', '.join(schema.get('required', []))}\n"
        + json.dumps(_skeleton(schema), ensure_ascii=False)
    )


# --------------------------------------------------------------------------- #
# 呼び出し
# --------------------------------------------------------------------------- #


class _Skip(Exception):
    """このモデルは今回あきらめる（次のモデルへ）"""


def _classify(error: Exception) -> str:
    text = str(error)
    low = text.lower()
    if "429" in text or "resource_exhausted" in low or "rate limit" in low or "quota" in low:
        return "quota"
    if ("404" in text or "not_found" in low or "not found" in low) and "model" in low:
        return "missing"
    if any(s in text for s in ("500", "502", "503", "504")) or any(
        s in low for s in ("unavailable", "internal", "deadline", "timed out", "timeout", "overloaded", "connection")
    ):
        return "transient"
    if "400" in text or "invalid_argument" in low:
        return "invalid"
    return "other"


def _retry_delay(text: str) -> float | None:
    match = re.search(r"retry in ([\d.]+)s", text) or re.search(r"'retryDelay': '(\d+)s'", text)
    return float(match.group(1)) if match else None


def _call_gemini(model: str, system_instruction: str, prompt: str, schema: dict, temperature: float, max_output_tokens: int, use_schema: bool = True) -> str:
    from google.genai import types

    gemma = model.startswith("gemma")
    cfg = {"temperature": temperature, "max_output_tokens": max_output_tokens}
    if gemma:
        # Gemma は system_instruction で 500 を返すことがある → 指示は本文に入れる
        contents = _plain_prompt(system_instruction, prompt, schema)
    else:
        contents = prompt
        cfg["system_instruction"] = system_instruction
    if use_schema:
        cfg.update(response_mime_type="application/json", response_schema=schema)
    response = _gemini_client().models.generate_content(model=model, contents=contents, config=types.GenerateContentConfig(**cfg))
    text = (response.text or "").strip()
    if not text:
        finish = getattr(response.candidates[0], "finish_reason", "?") if response.candidates else "?"
        raise ValueError(f"空のレスポンス (finish_reason={finish})")
    return text


def _call_openai_compatible(model: str, system_instruction: str, prompt: str, schema: dict, temperature: float, max_output_tokens: int) -> str:
    provider, name = model.split(":", 1)
    if provider == "groq":
        url, key = "https://api.groq.com/openai/v1/chat/completions", os.environ["GROQ_API_KEY"]
    elif provider == "cloudflare":
        url = f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}/ai/v1/chat/completions"
        key = os.environ["CLOUDFLARE_API_TOKEN"]
    else:
        url, key = "https://openrouter.ai/api/v1/chat/completions", os.environ["OPENROUTER_API_KEY"]
    payload = {
        "model": name,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": _plain_prompt("（system を参照）", prompt, schema)},
        ],
        "temperature": temperature,
        "max_tokens": min(max_output_tokens, 32000),
        "response_format": {"type": "json_object"},
    }
    try:
        headers = {"Authorization": f"Bearer {key}", "X-Title": "saisoku"}
        if provider == "groq":
            headers["User-Agent"] = GROQ_USER_AGENT
        data = _http_json(url, payload, headers)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"{error.code} {body}") from error
    return str(data["choices"][0]["message"]["content"] or "")


# --------------------------------------------------------------------------- #
# CLI（Claude Code / Codex）をサブスクリプションの枠で呼ぶ
# --------------------------------------------------------------------------- #


class CliQuotaError(RuntimeError):
    """利用上限・未ログインなど、この実行中はもう使えない状態"""


_CLI_LIMIT_PATTERNS = (
    "usage limit", "rate limit", "rate_limit", "limit reached", "hit your limit", "limit will reset",
    "resets at", "out of extra usage", "quota", "429", "too many requests",
)
_CLI_AUTH_PATTERNS = (
    "not logged in", "please run /login", "invalid api key", "oauth token", "authentication",
    "unauthorized", "401", "credit balance", "login required",
)


def _cli_check_limits(text: str) -> None:
    low = (text or "").lower()
    if any(p in low for p in _CLI_LIMIT_PATTERNS):
        raise CliQuotaError(f"利用上限: {text[:160]}")
    if any(p in low for p in _CLI_AUTH_PATTERNS):
        raise CliQuotaError(f"認証できません: {text[:160]}")


def _cli_env() -> dict:
    """Claude Code の中から呼ばれたときに親セッションの設定（CLAUDE_CODE_SIMPLE など）を引き継がない"""
    env = dict(os.environ)
    nested = "CLAUDECODE" in env
    for key in list(env):
        if key == "CLAUDE_CODE_OAUTH_TOKEN":
            continue
        # FAST_LANE_GH_TOKEN（速報レーンの確認用の GitHub トークン）も渡さない
        if key == "CLAUDECODE" or key.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")) or key in ("CLAUDE_PID", "CLAUDE_EFFORT", "FAST_LANE_GH_TOKEN"):
            env.pop(key)
        elif nested and key == "ANTHROPIC_BASE_URL":
            env.pop(key)
    return env


def _json_schema(schema: dict) -> dict:
    """Gemini 形式（"OBJECT" など大文字・propertyOrdering・nullable）を標準の JSON Schema へ"""
    if not isinstance(schema, dict):
        return schema
    out: dict = {}
    for key, value in schema.items():
        if key in ("propertyOrdering", "nullable", "format"):
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = value.lower()
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: _json_schema(v) for k, v in value.items()}
        elif key == "items":
            out["items"] = _json_schema(value)
        elif key == "anyOf" and isinstance(value, list):
            out["anyOf"] = [_json_schema(v) for v in value]
        else:
            out[key] = value
    if schema.get("nullable") and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    return out


def _call_claude(model: str, system_instruction: str, prompt: str, schema: dict, use_schema: bool = True) -> str:
    name = model.split(":", 1)[1]
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", name,
        "--system-prompt", system_instruction,
        "--tools", "",                 # ツールは一切使わせない
        "--strict-mcp-config",         # MCP サーバーも読まない
        "--setting-sources", "",       # ユーザー/プロジェクトの設定・フックを読まない
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns", "3",            # 構造化出力は内部で1手使うので少し余裕
    ]
    if use_schema and os.environ.get("CLAUDE_JSON_SCHEMA", "1") != "0":
        cmd += ["--json-schema", json.dumps(_json_schema(schema), ensure_ascii=False)]
        body = prompt
    else:
        body = _plain_prompt("（system を参照）", prompt, schema)
    try:
        proc = subprocess.run(
            cmd, input=body, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
            env=_cli_env(), cwd=tempfile.gettempdir(),  # リポジトリの CLAUDE.md を読ませない
        )
    except subprocess.TimeoutExpired as error:
        raise _Skip(f"timeout ({CLAUDE_TIMEOUT}s)") from error
    out, err = proc.stdout.strip(), proc.stderr.strip()
    try:
        wrapper = json.loads(out.splitlines()[-1] if out else "")
    except (json.JSONDecodeError, IndexError):
        _cli_check_limits(out + " " + err)
        raise RuntimeError(f"claude の出力を読めません (exit {proc.returncode}): {(err or out)[:200]}")
    result = wrapper.get("result")
    structured = wrapper.get("structured_output")
    if wrapper.get("is_error") or wrapper.get("subtype") != "success":
        detail = f"{wrapper.get('subtype')} {wrapper.get('api_error_status') or ''} {result or ''} {err}"
        _cli_check_limits(detail)
        if use_schema and str(wrapper.get("subtype", "")).startswith("error_max"):
            # 構造化出力で手数が尽きた → スキーマ無し（JSON だけを指示）で1回やり直す
            return _call_claude(model, system_instruction, prompt, schema, use_schema=False)
        raise RuntimeError(f"claude エラー: {detail.strip()[:200]}")
    if isinstance(structured, dict):
        return json.dumps(structured, ensure_ascii=False)
    text = str(result or "").strip()
    if not text:
        raise ValueError("claude の結果が空です")
    return text


def _call_codex(model: str, system_instruction: str, prompt: str, schema: dict) -> str:
    name = model.split(":", 1)[1]
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.txt"
        cmd = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "-C", tmp, "-o", str(last)]
        if name != "default":
            cmd += ["-m", name]
        cmd.append("-")  # 依頼は標準入力から
        try:
            proc = subprocess.run(
                cmd, input=_plain_prompt(system_instruction, prompt, schema), capture_output=True,
                text=True, timeout=CODEX_TIMEOUT,
            )
        except subprocess.TimeoutExpired as error:
            raise _Skip(f"timeout ({CODEX_TIMEOUT}s)") from error
        text = last.read_text("utf-8").strip() if last.exists() else ""
    if proc.returncode != 0 or not text:
        detail = (proc.stderr or proc.stdout or "").strip()
        _cli_check_limits(detail)
        if "unexpected argument" in detail or "unrecognized" in detail:
            raise CliQuotaError(f"この codex では非対応: {detail[:160]}")
        raise RuntimeError(f"codex エラー (exit {proc.returncode}): {detail[-200:]}")
    return text


def generate_json(
    system_instruction: str,
    prompt: str,
    schema: dict,
    *,
    temperature: float = 0.6,
    max_output_tokens: int = 16384,
    tier: str = "quality",
) -> tuple[dict, str]:
    """候補モデルを順に試し、必須キーがそろった JSON を返す。(データ, 使ったモデル名)"""
    last_error: Exception | None = None
    for model in models_for(tier):
        if model in _exhausted:
            continue
        if model.startswith("claude:") and tier != "quality" and claude_calls_today() >= _claude_daily_max():
            log(f"   [llm] {model}: 今日の上限 {_claude_daily_max()} 回に達したので使わない")
            continue
        use_schema = True
        attempt = 0
        while True:
            attempt += 1
            try:
                if model.startswith("claude:"):
                    text = _call_claude(model, system_instruction, prompt, schema)
                elif model.startswith("codex:"):
                    text = _call_codex(model, system_instruction, prompt, schema)
                elif ":" in model:
                    text = _call_openai_compatible(model, system_instruction, prompt, schema, temperature, max_output_tokens)
                else:
                    text = _call_gemini(model, system_instruction, prompt, schema, temperature, max_output_tokens, use_schema)
                _record_usage(model)
                data = parse_json_lenient(text)
                missing = missing_keys(data, schema)
                if missing:
                    raise _Skip(f"必須キー不足: {', '.join(missing[:6])}")
                return data, model
            except CliQuotaError as error:
                last_error = error
                _exhausted.add(model)
                log(f"   [llm] {model}: {str(error)[:160]}（今回はもう使わない）")
                break
            except _Skip as error:
                last_error = error
                log(f"   [llm] {model}: {error} -> 次のモデルへ")
                break
            except (ValueError, json.JSONDecodeError) as error:
                last_error = error
                log(f"   [llm] {model}: JSON を読めません（{str(error)[:80]}） -> 次のモデルへ")
                break
            except Exception as error:  # noqa: BLE001 - API 由来の例外を一括で扱う
                last_error = error
                kind = _classify(error)
                text = str(error)
                if kind == "quota":
                    wait = _retry_delay(text)
                    if wait is not None and wait <= MAX_SHORT_WAIT and "PerDay" not in text and attempt == 1:
                        log(f"   [llm] {model}: 毎分の上限 -> {wait:.0f}s 待って再試行")
                        time.sleep(wait + 1)
                        continue
                    _exhausted.add(model)
                    log(f"   [llm] {model}: 無料枠の上限（今回はもう使わない）")
                    break
                if kind == "missing":
                    _exhausted.add(model)
                    log(f"   [llm] {model}: モデルが見つかりません -> スキップ")
                    break
                if kind == "invalid" and use_schema and ":" not in model:
                    # response_schema 非対応のモデル → スキーマ無し（JSON だけを指示）で1回やり直す
                    log(f"   [llm] {model}: 400（{text[:80]}） -> スキーマ無しで再試行")
                    use_schema = False
                    continue
                if kind == "transient" and attempt <= TRANSIENT_RETRIES:
                    wait = min(20, 5 * attempt) + random.uniform(0, 2)
                    log(f"   [llm] {model}: 一時的なエラー（{text[:80]}） -> {wait:.0f}s 後に再試行")
                    time.sleep(wait)
                    continue
                log(f"   [llm] {model}: 失敗（{text[:120]}） -> 次のモデルへ")
                break
    raise RuntimeError(f"すべてのモデルで失敗しました: {last_error}")


def all_exhausted(tier: str) -> bool:
    return all(m in _exhausted for m in models_for(tier))


def _load_env_local() -> None:
    """ローカル実行用: .env.local の GEMINI_API_KEY などを環境変数へ（既に設定済みなら触らない）"""
    path = ROOT / ".env.local"
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        match = re.match(r"^(GEMINI_API_KEY|GOOGLE_API_KEY|GROQ_API_KEY|OPENROUTER_API_KEY)=(.+)$", line.strip())
        if match and not os.environ.get(match.group(1)):
            os.environ[match.group(1)] = match.group(2).strip().strip("\"'")


if __name__ == "__main__":
    _load_env_local()
    for tier_name in ("quality", "bulk"):
        models_for(tier_name)
    log(f"[llm] 今日の使用回数: {usage_today()}")
    log(f"[llm] Claude: {'有効' if claude_enabled() else '無効'}（今日 {claude_calls_today()} 回 / bulk 上限 {_claude_daily_max()}）")
    if "--ping" in sys.argv:
        demo_schema = {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}, "n": {"type": "INTEGER"}}, "required": ["answer", "n"]}
        result, used = generate_json("日本語で簡潔に答える。", "1+1は？ answer に一言の説明、n に数値。", demo_schema, tier=os.environ.get("LLM_PING_TIER", "bulk"), max_output_tokens=256)
        log(f"[llm] {used}: {result}")
