#!/usr/bin/env python3
"""無料枠のモデルを順番に使い回して JSON を生成するヘルパー。

  from llm import generate_json
  data, model = generate_json(system_text, prompt, schema, tier="bulk")

- tier="quality": main.py と同じ Gemini Flash 系（無料）
- tier="write"  : 通常記事の本文を書く段（2026-11-03）。いちばん強い無料モデルから: Gemini Flash 系（各1日20回の無料枠。
                  速報用に少し残す）→ Cloudflare / Groq の gpt-oss-120b → OpenRouter の無料モデル → 最後に軽量モデル（flash-lite → Gemma）。
                  軽量モデルは事実確認・文の手直しなど小さな作業（bulk）に回す
- tier="bulk"   : 無料枠の大きい軽量モデル（flash-lite → Gemma）→ quality → 外部無料API（キーがあれば）→ Codex（ローカルのみ）
- Claude API（クレジット）は月の予算いっぱいまで、段（purpose）ごとにモデルを分けて使う（2026-11-03 オーナー決定）:
    data, model = generate_json(system, prompt, schema, tier="write", purpose="core")
    Opus 5.5  : "breaking"（速報・大きなニュース）/ "video"（動画の台本）        … 予算が苦しくても下げない
    Sonnet 5.5: "sns"（SNS に出しそうな記事）/ "core"（読者像の中心の話題の記事）/ "video_review"（台本のレビュー）
    Haiku 5.5 : "rest"（ほかの記事）/ "helper"（事実確認の指摘の手直し・FAQ の書き足し・SNS の問いかけ）
  予算は CLAUDE_API_MONTHLY_BUDGET_USD（既定 $95。期間はクレジットの補充日 4日〜翌月3日）。毎回、今日の目安額
  （残り予算÷残り日数）の残りを見込み回数で配り（api_plan）、足りなければ 中心→Haiku、SNS→Haiku、最後に その他→無料 の順に下げ、
  余れば SNS→Opus、中心→Opus、その他→Sonnet の順に上げる（割合で混ぜる）。使用額は state/claude_api_usage*.json に
  段ごとに記録し、実行ログに1行の要約を出す。クレジット切れは次の補充日まで使わず、API の失敗で処理は止めない。
  API が使えない・失敗したときは、速報・SNS・動画だけ Claude CLI、それ以外は無料モデル。purpose なしは無料モデルだけ。
- Claude CLI は Claude Code CLI（`claude -p`）をサブスクリプションの枠で呼ぶ。使えるのは
  `claude` が PATH にあり、かつ CLAUDE_CODE_OAUTH_TOKEN（GitHub Actions。`claude setup-token` で発行）
  または LLM_USE_LOCAL_CLAUDE=1（手元の Mac）のときだけ。CLAUDE_MODEL でモデルを変更（既定 sonnet）。
  1日 CLAUDE_DAILY_MAX 回まで（速報 purpose="breaking" は上限なし）。
- Codex は `codex` が PATH にあり LLM_USE_LOCAL_CODEX=1 のときだけ（手元の ChatGPT ログイン用。CI では使わない）。
- 429 / RESOURCE_EXHAUSTED のモデルはその実行中は使わない。404 は飛ばす。5xx は短く2回まで再試行。
- モデルごとの1日の使用回数を content/llm_usage.json に記録（直近7日分）。

  python3 scripts/llm.py            # 使えるモデル一覧と今日の使用回数を表示
  python3 scripts/llm.py --ping     # bulk で小さな JSON を1回生成して動作確認
  python3 scripts/llm.py --api-ping # Claude API（Haiku）に小さな依頼を1回だけ送って動作確認
  python3 scripts/llm.py --merge-api-usage MINE DEST  # 別の実行の API 使用量を DEST に足す（同じ実行の分は大きい方）
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    # Gemini の無料枠は太平洋時間の 0 時にリセットされる
    QUOTA_TZ = ZoneInfo("America/Los_Angeles")
except Exception:  # noqa: BLE001 - tzdata が無い環境
    QUOTA_TZ = None

ROOT = Path(__file__).resolve().parent.parent
USAGE_PATH = ROOT / "content" / "llm_usage.json"
# 本文を書く段で時間切れになったモデルと時刻（混雑中の Flash を、次の実行でも数分待たないように。記事と一緒にコミットされる）
SLOW_PATH = ROOT / "content" / "llm_slow.json"
SLOW_COOLDOWN = timedelta(minutes=int(os.environ.get("LLM_SLOW_COOLDOWN_MIN", "60")))
REQUEST_TIMEOUT_MS = 240_000
# 本文を書く段（write）で Gemini を待つ上限。Flash 系は混雑すると数分かかる・504 になるので、待ちすぎずに次の強いモデルへ
WRITE_TIMEOUT_MS = int(os.environ.get("LLM_WRITE_TIMEOUT_MS", "150000"))
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
# 無料枠の1日の回数（太平洋時間で区切る）。content/llm_usage.json の今日の回数がこれに届いたモデルは、呼ばずに飛ばす
# （2026-11-03 に 429 の応答で確認: Gemini Flash 系は GenerateRequestsPerDayPerProjectPerModel-FreeTier = 20）。
# 環境変数 LLM_DAILY_LIMIT_<モデル名の英数字を _ に> で変更できる（0 = 数えない）
FREE_DAILY_LIMITS = {m: 20 for m in QUALITY_MODELS}
# 本文を書く段（write）では、Flash 系の枠をこの回数だけ残す（速報・大きなニュースの予備。Claude が使えないとき quality で作る）
WRITE_RESERVE = 3
GROQ_USER_AGENT = "saisoku-ai-news/1.0 (+https://ai-tech-express.jp)"

CLAUDE_DEFAULT_MODEL = "sonnet"
CLAUDE_DAILY_MAX_DEFAULT = 40  # bulk での1日の上限（オーナーの対話利用と枠を共有するため）
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "600"))
CODEX_TIMEOUT = int(os.environ.get("CODEX_TIMEOUT", "600"))

_client = None
_available: set[str] | None = None
_exhausted: set[str] = set()
_limit_logged: set[tuple[str, str]] = set()


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
    _record_workflow_usage(model)


def usage_today() -> dict:
    """{model: 今日（太平洋時間）の呼び出し回数}。記事のジョブの記録と、ほかのジョブの記録（ジョブごとのファイル）の多い方"""
    total = dict(_load_usage().get(_today(), {}))
    for model, count in workflow_usage_today().items():
        total[model] = max(total.get(model, 0), count)
    return total


# --------------------------------------------------------------------------- #
# ジョブ（ワークフロー）ごとの無料枠の取り分（2026-11-03 オーナー「何回も記事作成が止まるのはやめたい」）
# --------------------------------------------------------------------------- #
# 1日の回数が決まっている無料モデル（FREE_DAILY_LIMITS）を、ジョブごとに分ける。記事・速報は取り分の上限なし、
# ほかのジョブは下の割合まで（合計 40%）。だから記事・速報には、少なくとも 60% が必ず残る。
# ジョブ名は環境変数 LLM_WORKFLOW（無ければ GitHub のワークフロー名から）。
# 記録: 記事のジョブ（auto_run の中の articles・breaking・sns_helper・images・guides・products）は content/llm_usage_wf.json（記事と一緒にコミット）、
# ほかのジョブは state/llm_usage_<ジョブ>.json（各ワークフローがコミット）。
RESERVED_WORKFLOWS = {"articles", "breaking"}
WORKFLOW_SHARES = {"video": 0.10, "evergreen": 0.10, "weekly": 0.05, "guides": 0.05, "sns_helper": 0.05, "images": 0.05}
WORKFLOW_SHARE_DEFAULT = 0.0  # 知らないジョブは数のある無料モデルを使わない（数の無いモデル・Claude API は使える）
AUTO_RUN_WORKFLOWS = {"articles", "breaking", "sns_helper", "images", "guides", "products"}
LLM_STATE_DIR = ROOT / "state"
WORKFLOW_BY_NAME = {"Auto Article Pipeline": "articles", "SNS video v3 (manual)": "video", "Weekly roundup": "weekly"}


def current_workflow() -> str:
    name = os.environ.get("LLM_WORKFLOW")
    if name:
        return re.sub(r"[^a-z0-9_]", "", name.lower()) or "local"
    gh = os.environ.get("GITHUB_WORKFLOW")
    if gh:
        return WORKFLOW_BY_NAME.get(gh) or re.sub(r"[^a-z0-9_]+", "_", gh.lower()).strip("_") or "other"
    return "local"


def _workflow_usage_path(workflow: str) -> Path:
    if workflow in AUTO_RUN_WORKFLOWS or workflow == "local":
        return USAGE_PATH.with_name("llm_usage_wf.json")  # content/llm_usage_wf.json
    return LLM_STATE_DIR / f"llm_usage_{workflow}.json"


def _record_workflow_usage(model: str) -> None:
    workflow = current_workflow()
    path = _workflow_usage_path(workflow)
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    day = data.setdefault(_today(), {}).setdefault(workflow, {})
    day[model] = day.get(model, 0) + 1
    data = {d: data[d] for d in sorted(data)[-3:]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    except OSError:
        pass


def workflow_usage_by_job() -> dict[str, dict[str, int]]:
    """{ジョブ: {モデル: 今日の回数}}（全ジョブのファイルを合わせて）"""
    out: dict[str, dict[str, int]] = {}
    paths = list(dict.fromkeys([USAGE_PATH.with_name("llm_usage_wf.json")] + sorted(LLM_STATE_DIR.glob("llm_usage_*.json"))))
    for path in paths:
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for workflow, models in (data.get(_today()) or {}).items():
            if isinstance(models, dict):
                acc = out.setdefault(workflow, {})
                for model, count in models.items():
                    acc[model] = acc.get(model, 0) + int(count or 0)
    return out


def workflow_usage_today() -> dict[str, int]:
    total: dict[str, int] = {}
    for models in workflow_usage_by_job().values():
        for model, count in models.items():
            total[model] = total.get(model, 0) + count
    return total


def workflow_share_cap(model: str, workflow: str | None = None) -> int | None:
    """このジョブがこのモデルを今日使ってよい回数（None = 取り分の上限なし）"""
    workflow = workflow or current_workflow()
    limit = daily_limit(model)
    if limit <= 0 or workflow in RESERVED_WORKFLOWS or workflow == "local":
        return None
    share = WORKFLOW_SHARES.get(workflow, WORKFLOW_SHARE_DEFAULT)
    return int(limit * share)


def over_workflow_share(model: str, workflow: str | None = None) -> bool:
    workflow = workflow or current_workflow()
    cap = workflow_share_cap(model, workflow)
    if cap is None:
        return False
    return workflow_usage_by_job().get(workflow, {}).get(model, 0) >= cap


def _load_slow() -> dict:
    try:
        data = json.loads(SLOW_PATH.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def mark_slow(model: str, now: datetime | None = None) -> None:
    """本文を書く段で時間切れになったモデルを記録する（SLOW_COOLDOWN のあいだ本文の段では使わない）"""
    now = now or datetime.now(timezone.utc)
    data = {m: t for m, t in _load_slow().items() if _slow_until(t, now)}
    data[model] = now.isoformat(timespec="seconds")
    try:
        SLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
        SLOW_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    except OSError:
        pass


def _slow_until(stamp: str, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(stamp) + SLOW_COOLDOWN > now
    except (TypeError, ValueError):
        return False


def recently_slow(model: str, now: datetime | None = None) -> bool:
    stamp = _load_slow().get(model)
    return bool(stamp) and _slow_until(stamp, now or datetime.now(timezone.utc))


def daily_limit(model: str) -> int:
    """無料枠の1日の回数（分からなければ 0 = 数えない）"""
    env = "LLM_DAILY_LIMIT_" + re.sub(r"[^A-Za-z0-9]", "_", model).upper()
    try:
        return int(os.environ[env])
    except (KeyError, ValueError):
        return FREE_DAILY_LIMITS.get(model, 0)


def over_daily_limit(model: str, tier: str = "", used: dict | None = None) -> bool:
    """今日の無料枠を使い切った（本文を書く段では、速報用の予備 WRITE_RESERVE 回を残した）モデルか"""
    limit = daily_limit(model)
    if limit <= 0:
        return False
    used = usage_today() if used is None else used
    return used.get(model, 0) >= limit - (WRITE_RESERVE if tier == "write" else 0)


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
        # Claude（CLI）はここに入れない。visibility="high" の呼び出しだけが generate_json の中で先頭に足す
        # どの段も Gemini だけに頼らない（Gemini が全部 429 でも止まらないように。2026-11-03）:
        #   Gemini → Cloudflare Workers AI（gpt-oss-120b など）→ OpenRouter の無料モデル → Groq
        bulk = [m for m in BULK_MODELS if m in have]
        cloudflare, openrouter, groq = _cloudflare_models(), _openrouter_models(), _groq_models()
        others = cloudflare + openrouter + groq
        if tier in ("quality", "write"):
            # 強い Gemini（Flash）→ ほかの会社のいちばん強いモデル → 軽量の Gemini（flash-lite・Gemma）→ ほかの会社の残り
            chosen = list(dict.fromkeys(quality + cloudflare[:1] + openrouter + groq[:1] + bulk + cloudflare[1:] + groq[1:])) + _codex_models()
        else:
            chosen = list(dict.fromkeys(bulk + quality)) + others + _codex_models()
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


def _call_gemini(model: str, system_instruction: str, prompt: str, schema: dict, temperature: float, max_output_tokens: int,
                 use_schema: bool = True, timeout_ms: int | None = None) -> str:
    from google.genai import types

    gemma = model.startswith("gemma")
    cfg = {"temperature": temperature, "max_output_tokens": max_output_tokens}
    if timeout_ms:
        cfg["http_options"] = types.HttpOptions(timeout=timeout_ms)
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


# --------------------------------------------------------------------------- #
# Claude API（Claude Platform のクレジット）。月の予算いっぱいまで、段（tier）ごとにモデルを分けて使う
# --------------------------------------------------------------------------- #

API_PREFIX = "anthropic-api:"
API_STATE_DIR = ROOT / "state"
JST = timezone(timedelta(hours=9))
# 1M トークンあたりの米ドル: (入力, 出力, キャッシュ読み出し)。キャッシュ書き込みは入力の 1.25 倍（5分）
API_PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
API_CACHE_WRITE_MULT = 1.25
# モデルの段: 0=無料モデル（API を使わない）/ 1=Haiku / 2=Sonnet / 3=Opus。環境変数 CLAUDE_API_MODEL_<LOW|MID|TOP> で変更
API_LEVEL_NAMES = ("free", "low", "mid", "top")
API_LEVEL_LABELS = ("無料", "Haiku", "Sonnet", "Opus")
API_LEVEL_DEFAULTS = {1: "claude-haiku-5-5", 2: "claude-sonnet-5-5", 3: "claude-opus-5-5"}
# purpose（段）: (いつもの段, 下げられる下限, 上げられる上限, 守る段か, 表示名)
#   守る段（速報・動画・補助）は予算が苦しくても下げない（月の予算＋予備 API_PROTECTED_MARGIN まで使う）
API_TIERS = {
    "breaking": (3, 3, 3, True, "速報"),
    "video": (3, 3, 3, True, "動画"),
    "video_review": (2, 2, 2, True, "動画レビュー"),
    "check": (1, 1, 2, True, "確認"),         # 品質ゲートの事実確認・続報の判定（Haiku。余れば Sonnet）
    "helper": (1, 1, 1, True, "補助"),        # 指摘の手直し・言葉の手直し・読みやすさの書き直し・FAQ の書き足し・SNS の問いかけ など
    "sns": (2, 1, 3, False, "SNS"),          # SNS に出しそうな記事
    "core": (2, 1, 3, False, "中心"),         # 読者像の中心の話題（AI・仕事とお金・ビジネス・投資・税金・補助金・起業）
    "rest": (1, 0, 2, False, "その他"),       # ほかの記事（スポーツ・暮らし・エンタメ・科学など）
}
# Claude CLI（サブスクリプション）も使ってよい段（2026-11-03 オーナー決定: 速報・SNS・動画だけ）
API_CLI_PURPOSES = {"breaking", "sns", "video", "video_review"}
# 予算の配り方（優先順）。下の段ほど後回し＝予算が足りないと先に削られる:
#   削る順 = 中心 Sonnet→Haiku → SNS Sonnet→Haiku → 最後に その他 Haiku→無料（月 $3 ほどなので最後まで残す。速報・動画は削らない）
#   余るとき = さらに SNS→Opus → 中心→Opus → その他→Sonnet と上げる（CLAUDE_API_UPGRADES=0 で上げない）
API_STEPS = [("rest", 0, 1), ("sns", 1, 2), ("core", 1, 2)]
API_UPGRADE_STEPS = [("check", 1, 2), ("sns", 2, 3), ("core", 2, 3), ("rest", 1, 2)]
# 1日の呼び出し回数の見込み（実績が2日分たまるまでの仮の値。作り直しも1回と数える）
API_DEMAND_DEFAULT = {"breaking": 7, "video": 5, "video_review": 5, "check": 90, "helper": 80, "sns": 13, "core": 27, "rest": 21}
# 1回あたりの料金の見込み（Sonnet で書いたとき。実績が3回分たまったらその平均を使う）。ほかのモデルは料金の比で換算
# 2026-11-03 の実績: 速報 Opus 平均 $0.25/回・SNS Sonnet $0.09/回・動画 Opus $0.11/回（入力 約1.5万・出力 約0.5〜0.9万トークン）
API_COST_MID_DEFAULT = {"breaking": 0.125, "video": 0.055, "video_review": 0.02, "check": 0.045, "helper": 0.04, "sns": 0.09, "core": 0.09, "rest": 0.09}
API_LEVEL_COST_RATIO = {1: 0.05, 2: 1.0, 3: 2.0}
# 1日の回数の安全上限（暴走よけ。環境変数 CLAUDE_API_DAILY_CALLS_<段> で変更）
API_DAILY_CALLS = {"breaking": 20, "video": 20, "video_review": 20, "check": 300, "helper": 300, "sns": 30, "core": 60, "rest": 80}
API_MONTHLY_BUDGET_DEFAULT = 95.0  # CLAUDE_API_MONTHLY_BUDGET_USD（Ultra なら 190 くらい）
API_PROTECTED_MARGIN = 4.0          # 守る段だけが予算を超えて使ってよい額（クレジット $100 との差の中）
API_REFILL_DAY_DEFAULT = 4           # クレジットが補充される日（毎月）。期間は 4日〜翌月3日
API_TIMEOUT = 180
_EXHAUSTED_PATTERNS = (
    "credit balance", "billing", "insufficient credit", "usage limit", "spend limit", "regain access",
    "out of credits", "payment",
)

_api_client = None
_api_off: str | None = None   # この実行ではもう API を使わない理由
_api_transient_failures = 0
_local_run_key: str | None = None
_api_summary_logged: set[str] = set()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def api_level_model(level: int) -> str | None:
    if level <= 0:
        return None
    return os.environ.get(f"CLAUDE_API_MODEL_{API_LEVEL_NAMES[level].upper()}") or API_LEVEL_DEFAULTS[level]


def api_cost(model: str, usage) -> float:
    """usage（input_tokens / output_tokens / cache_*）から米ドルを見積もる。知らないモデルは Opus の値で多めに数える"""
    price_in, price_out, price_read = API_PRICES.get(model, API_PRICES["claude-opus-5-5"])

    def n(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        return int(value or 0)

    return (
        n("input_tokens") * price_in
        + n("cache_creation_input_tokens") * price_in * API_CACHE_WRITE_MULT
        + n("cache_read_input_tokens") * price_read
        + n("output_tokens") * price_out
    ) / 1_000_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refill_day() -> int:
    return min(28, max(1, _env_int("CLAUDE_API_REFILL_DAY", API_REFILL_DAY_DEFAULT)))


def _period_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """いまのクレジットの期間（補充日 0:00 UTC 〜 次の補充日 0:00 UTC）"""
    now = now or _now()
    day = _refill_day()
    start = now.replace(day=day, hour=0, minute=0, second=0, microsecond=0)
    if now < start:
        start = (start - timedelta(days=day + 3)).replace(day=day)
    end = (start + timedelta(days=32)).replace(day=day)
    return start, end


def _jst_day(now: datetime | None = None) -> str:
    return (now or _now()).astimezone(JST).strftime("%Y-%m-%d")


def _ledger_path() -> Path:
    name = re.sub(r"[^a-z0-9_-]", "", (os.environ.get("CLAUDE_API_LEDGER") or "").lower())
    return API_STATE_DIR / (f"claude_api_usage_{name}.json" if name else "claude_api_usage.json")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _run_key() -> str:
    run = os.environ.get("GITHUB_RUN_ID")
    if run:
        return f"gh-{run}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
    global _local_run_key
    if not _local_run_key:
        _local_run_key = f"local-{_now():%Y%m%d%H%M%S}-{os.getpid()}"
    return _local_run_key


def _monthly_budget() -> float:
    return _env_float("CLAUDE_API_MONTHLY_BUDGET_USD", API_MONTHLY_BUDGET_DEFAULT)


def api_ledger_summary(now: datetime | None = None) -> dict:
    """state/claude_api_usage*.json（記事の回・動画の回など書き手ごとのファイル）を合計した、今の期間の状況"""
    now = now or _now()
    start, end = _period_bounds(now)
    first_day = start.astimezone(JST).strftime("%Y-%m-%d")
    prev_first = _period_bounds(start - timedelta(days=1))[0].astimezone(JST).strftime("%Y-%m-%d")
    today = _jst_day(now)
    spent = spent_today = 0.0
    calls_today: dict[str, int] = {}
    req_today: dict[str, int] = {}
    tier_today: dict[str, float] = {}
    req_by_day: dict[str, dict[str, int]] = {}
    cost_stats: dict[str, list[float]] = {}  # "段|モデルの段" → [回数, 米ドル]（今と前の期間）
    exhausted_until, reason = None, ""
    for path in sorted(API_STATE_DIR.glob("claude_api_usage*.json")):
        data = _read_json(path)
        for day, runs in (data.get("runs") or {}).items():
            if not isinstance(runs, dict) or day < prev_first:
                continue
            for entry in runs.values():
                for key, stat in (entry.get("by") or {}).items():
                    acc = cost_stats.setdefault(key, [0, 0.0])
                    acc[0] += int(stat.get("n") or 0)
                    acc[1] += float(stat.get("usd") or 0)
                if day != today:
                    for tier, count in (entry.get("req") or {}).items():
                        req_by_day.setdefault(day, {})[tier] = req_by_day.setdefault(day, {}).get(tier, 0) + int(count or 0)
                if day < first_day:
                    continue
                usd = float(entry.get("usd") or 0)
                spent += usd
                if day == today:
                    spent_today += usd
                    for tier, count in (entry.get("calls") or {}).items():
                        calls_today[tier] = calls_today.get(tier, 0) + int(count or 0)
                    for tier, count in (entry.get("req") or {}).items():
                        req_today[tier] = req_today.get(tier, 0) + int(count or 0)
                    for key, stat in (entry.get("by") or {}).items():
                        tier = key.split("|", 1)[0]
                        tier_today[tier] = tier_today.get(tier, 0) + float(stat.get("usd") or 0)
        until = data.get("exhausted_until")
        if until:
            try:
                when = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
            except ValueError:
                continue
            if when > now and (exhausted_until is None or when > exhausted_until):
                exhausted_until, reason = when, str(data.get("exhausted_reason") or "")
    budget = _monthly_budget()
    days_left = max(1, (end.astimezone(JST).date() - now.astimezone(JST).date()).days)
    daily = os.environ.get("CLAUDE_API_DAILY_BUDGET_USD")
    daily_limit = _env_float("CLAUDE_API_DAILY_BUDGET_USD", 0) if daily else max(0.0, (budget - (spent - spent_today)) / days_left)
    # 1日の呼び出し回数の見込み: 直近7日（今日を除く）の実績の平均。2日分たまるまでは仮の値
    recent = sorted(req_by_day)[-7:]
    demand = dict(API_DEMAND_DEFAULT)
    if len(recent) >= 2:
        for tier in API_TIERS:
            demand[tier] = sum(req_by_day[d].get(tier, 0) for d in recent) / len(recent)
    return {
        "period_start": start.strftime("%Y-%m-%d"), "period_end": end.strftime("%Y-%m-%d"),
        "budget": budget, "spent": round(spent, 4), "spent_today": round(spent_today, 4),
        "daily_limit": round(daily_limit, 4), "calls_today": calls_today, "req_today": req_today,
        "tier_today": {k: round(v, 4) for k, v in tier_today.items()}, "days_left": days_left,
        "demand": demand, "cost_stats": cost_stats,
        "exhausted_until": exhausted_until.isoformat() if exhausted_until else None, "exhausted_reason": reason,
    }


def api_call_cost(tier: str, level: int, summary: dict | None = None) -> float:
    """その段をそのモデルで1回書く料金の見込み（実績が3回以上あれば平均）"""
    if level <= 0:
        return 0.0
    stat = (summary or {}).get("cost_stats", {}).get(f"{tier}|{level}")
    if stat and stat[0] >= 3:
        return stat[1] / stat[0]
    return API_COST_MID_DEFAULT.get(tier, 0.10) * API_LEVEL_COST_RATIO[level]


def api_plan(now: datetime | None = None, summary: dict | None = None) -> dict:
    """今日のこれからの呼び出しを、どの段をどのモデルで書くかの配分。

    今日の目安額（残り予算÷残り日数）の残りを、今日の残り時間ぶんの見込み回数で配る:
    まず守る段（速報・動画・補助）と、下げられる段の下限（SNS・中心=Haiku、その他=無料）を確保し、
    残りを API_STEPS の順（その他→Haiku、SNS→Sonnet、中心→Sonnet）、さらに余れば API_UPGRADE_STEPS の順に配る。
    1つの段で足りなくなったら、そこで割合（例: 中心の4割だけ Sonnet）にして止める。
    戻り値の levels は段ごとの小数（例: 1.4 = 4割を1つ上のモデルで）"""
    now = now or _now()
    s = summary or api_ledger_summary(now)
    local = now.astimezone(JST)
    left = max(1 / 24, 1 - (local.hour * 60 + local.minute) / 1440)  # 今日の残り時間の割合
    levels = {tier: float(spec[1]) for tier, spec in API_TIERS.items()}
    room = s["daily_limit"] - s["spent_today"]
    room -= sum(s["demand"].get(t, 0) * api_call_cost(t, int(levels[t]), s) for t in API_TIERS) * left
    steps = API_STEPS + ([] if os.environ.get("CLAUDE_API_UPGRADES") == "0" else API_UPGRADE_STEPS)
    if s["spent"] >= s["budget"]:
        steps = []
    for tier, low, high in steps:
        need = s["demand"].get(tier, 0) * (api_call_cost(tier, high, s) - api_call_cost(tier, low, s)) * left
        share = 1.0 if need <= 0 else max(0.0, min(1.0, room / need))
        levels[tier] = low + share
        room -= share * need
        if share < 1.0:
            break
    if s["spent"] >= s["budget"]:  # 今月の予算に到達: 下げられる段は API を使わない（守る段だけ予備の範囲で続ける）
        for tier, spec in API_TIERS.items():
            if not spec[3]:
                levels[tier] = 0.0
    return {"levels": levels, "room": round(room, 4), "summary": s}


def api_summary_line(now: datetime | None = None) -> str:
    """実行ログに出す1行の要約（今日の段ごとの使用額と配分）"""
    plan = api_plan(now)
    s = plan["summary"]
    spent = " / ".join(f"{API_TIERS[t][4]} ${s['tier_today'].get(t, 0):.2f}" for t in API_TIERS if s["tier_today"].get(t))
    mix = []
    for tier in ("sns", "core", "rest", "check"):
        v = plan["levels"][tier]
        k = int(v)
        r = v - k
        mix.append(f"{API_TIERS[tier][4]} {API_LEVEL_LABELS[k]}" + (f"→{API_LEVEL_LABELS[k + 1]} {r:.0%}" if r > 0.005 and k < 3 else ""))
    return (
        f"[llm] Claude API {_jst_day(now)}: 期間 {s['period_start']}〜 ${s['spent']:.2f}/${s['budget']:.0f}（残り{s['days_left']}日）"
        f" / 今日 ${s['spent_today']:.2f}（目安 ${s['daily_limit']:.2f}）{(' = ' + spent) if spent else ''}"
        f" / 配分: {' ・ '.join(mix)}{' / クレジット切れ ' + s['exhausted_until'][:16] if s['exhausted_until'] else ''}"
    )


def _daily_calls_cap(tier: str) -> int:
    return max(0, _env_int(f"CLAUDE_API_DAILY_CALLS_{tier.upper()}", API_DAILY_CALLS.get(tier, 0)))


def api_block_reason(purpose: str, now: datetime | None = None, summary: dict | None = None) -> str | None:
    """この段で、いま Claude API を使えない理由（モデルの配分の前の確認。使えるなら None）"""
    if os.environ.get("CLAUDE_API_DISABLE") == "1":
        return "CLAUDE_API_DISABLE=1"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY が未設定"
    if _api_off:
        return _api_off
    spec = API_TIERS.get(purpose)
    if not spec:
        return f"purpose={purpose!r} は API の対象外"
    s = summary or api_ledger_summary(now)
    if s["exhausted_until"]:
        return f"クレジット切れの印（{s['exhausted_until'][:16]} まで: {s['exhausted_reason'][:60]}）"
    limit = s["budget"] + (_env_float("CLAUDE_API_PROTECTED_MARGIN_USD", API_PROTECTED_MARGIN) if spec[3] else 0)
    if s["spent"] >= limit:
        return f"今月の予算 ${limit:.0f} に到達（${s['spent']:.2f}）"
    cap = _daily_calls_cap(purpose)
    if s["calls_today"].get(purpose, 0) >= cap:
        return f"{purpose} の今日の安全上限 {cap} 回に到達"
    return None


def api_choose(purpose: str, now: datetime | None = None, record: bool = True) -> tuple[str | None, str]:
    """この1回を API のどのモデルで書くか（None なら API を使わない）と、その理由。
    配分が小数（例: 中心 1.4）なら、今日のその段の何回目かで均等に上のモデルを混ぜる（4割なら10回に4回）"""
    s = api_ledger_summary(now)
    why = api_block_reason(purpose, now, s)
    if why:
        return None, why
    if record:
        _record_request(purpose, now)
        day = _jst_day(now)
        if day not in _api_summary_logged:
            _api_summary_logged.add(day)
            log(api_summary_line(now))
    level_value = api_plan(now, s)["levels"][purpose]
    base = int(level_value)
    share = level_value - base
    n = s["req_today"].get(purpose, 0)  # 今日すでに来た回数（この回を含まない）
    level = base + 1 if share > 0 and int((n + 1) * share + 1e-9) > int(n * share + 1e-9) else base
    model = api_level_model(min(level, 3))
    if not model:
        return None, f"{API_TIERS[purpose][4]}は今は無料モデル（予算の配分）"
    return model, ""


def _save_ledger(data: dict) -> None:
    path = _ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")


def _prune_ledger(data: dict, now: datetime | None = None) -> dict:
    """前の期間より古い日は合計だけ history に残して消す（ファイルを小さく保つ）"""
    start, _ = _period_bounds(now)
    prev_start, _ = _period_bounds(start - timedelta(days=1))
    keep_from = prev_start.astimezone(JST).strftime("%Y-%m-%d")
    history = data.setdefault("history", {})
    runs = data.setdefault("runs", {})
    for day in [d for d in runs if d < keep_from]:
        month = day[:7]
        history[month] = round(history.get(month, 0) + sum(float(e.get("usd") or 0) for e in runs[day].values()), 4)
        del runs[day]
    return data


def _ledger_entry(data: dict, now: datetime | None = None) -> dict:
    return data.setdefault("runs", {}).setdefault(_jst_day(now), {}).setdefault(_run_key(), {})


def _record_request(purpose: str, now: datetime | None = None) -> None:
    """来た回数（API を使ったかどうかに関係なく）。翌日以降の「1日の見込み回数」に使う"""
    data = _read_json(_ledger_path())
    entry = _ledger_entry(data, now)
    entry.setdefault("req", {})[purpose] = entry.get("req", {}).get(purpose, 0) + 1
    _save_ledger(_prune_ledger(data, now))


def _model_level(model: str) -> int:
    for level in (1, 2, 3):
        if api_level_model(level) == model:
            return level
    return {"claude-haiku-5-5": 1, "claude-sonnet-5-5": 2}.get(model, 3)


def record_api_usage(purpose: str, model: str, usage, now: datetime | None = None) -> float:
    """1回の呼び出しの使用量を自分の実行の欄に足す（合計・段ごと・段×モデルごと）。戻り値はその回の米ドル"""
    usd = api_cost(model, usage)
    data = _read_json(_ledger_path())
    entry = _ledger_entry(data, now)
    entry["usd"] = round(float(entry.get("usd") or 0) + usd, 6)
    entry.setdefault("calls", {})[purpose] = entry.get("calls", {}).get(purpose, 0) + 1
    stat = entry.setdefault("by", {}).setdefault(f"{purpose}|{_model_level(model)}", {"n": 0, "usd": 0.0})
    stat["n"] += 1
    stat["usd"] = round(stat["usd"] + usd, 6)

    def n(name: str) -> int:
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
        return int(value or 0)

    for key, name in (("in", "input_tokens"), ("out", "output_tokens"), ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens")):
        entry[key] = entry.get(key, 0) + n(name)
    _save_ledger(_prune_ledger(data, now))
    return usd


def mark_api_exhausted(reason: str, now: datetime | None = None) -> str:
    """クレジット切れ: 次の補充日まで使わない印を付ける（補充直後の24時間は、補充の遅れを考えて3時間だけ）"""
    now = now or _now()
    start, end = _period_bounds(now)
    until = now + timedelta(hours=3) if now - start < timedelta(hours=24) else end
    data = _read_json(_ledger_path())
    data["exhausted_until"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
    data["exhausted_reason"] = reason[:160]
    _save_ledger(data)
    return data["exhausted_until"]


def merge_api_usage(mine: dict, dest: dict) -> dict:
    """同じ実行（run）の欄は大きい方、ほかは足し合わせる。クレジット切れの印は遅い方"""
    out = json.loads(json.dumps(dest)) if dest else {}
    runs = out.setdefault("runs", {})

    def size(entry: dict) -> tuple:
        return (float(entry.get("usd") or 0), sum((entry.get("req") or {}).values()))

    for day, entries in (mine.get("runs") or {}).items():
        target = runs.setdefault(day, {})
        for key, entry in entries.items():
            if size(entry) >= size(target.get(key) or {}):
                target[key] = entry
    for month, usd in (mine.get("history") or {}).items():
        out.setdefault("history", {})[month] = max(float(usd or 0), float(out.get("history", {}).get(month) or 0))
    if str(mine.get("exhausted_until") or "") > str(out.get("exhausted_until") or ""):
        out["exhausted_until"] = mine["exhausted_until"]
        out["exhausted_reason"] = mine.get("exhausted_reason", "")
    return out


def _api_schema(schema: dict) -> dict:
    """構造化出力（output_config.format）用: 標準の JSON Schema にし、object には additionalProperties: false、
    対応していない制約（文字数・数値の範囲など）は外す"""
    unsupported = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
                   "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "example", "title"}

    def walk(node):
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key in unsupported:
                continue
            if key in ("properties", "$defs", "definitions") and isinstance(value, dict):
                # ここは「項目名 → 形」の表。項目名（title・pattern など）は消さない（2026-11-03 title が消えた不具合）
                out[key] = {name: walk(sub) for name, sub in value.items()}
            else:
                out[key] = walk(value)
        kind = out.get("type")
        if kind == "object" or (isinstance(kind, list) and "object" in kind):
            props = out.get("properties") or {}
            out["properties"] = props
            out["required"] = [k for k in out.get("required", []) if k in props]
            out["additionalProperties"] = False
        return out

    return walk(_json_schema(schema))


def _get_api_client():
    global _api_client
    if _api_client is None:
        import anthropic  # noqa: PLC0415 - 使うときだけ読み込む（未インストールなら API を使わない）

        _api_client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=_env_float("CLAUDE_API_TIMEOUT", API_TIMEOUT),
            max_retries=1,
        )
    return _api_client


def _api_error_kind(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    etype = str(getattr(error, "type", "") or "")
    body = getattr(error, "body", None)
    text = f"{error} {etype} {json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else body or ''}".lower()
    if status == 402 or etype == "billing_error" or "billing_error" in text or any(p in text for p in _EXHAUSTED_PATTERNS):
        return "exhausted"
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "missing"
    if status == 400 or status == 413 or status == 422:
        return "invalid"
    return "transient"  # 429・5xx・529・通信・時間切れ（SDK が1回だけ自動で再試行したあと）


def _api_request(model: str, system_instruction: str, prompt: str, schema: dict, max_tokens: int, use_schema: bool):
    output_config: dict = {"effort": os.environ.get("CLAUDE_API_EFFORT") or "medium"}
    body = prompt
    if use_schema:
        output_config["format"] = {"type": "json_schema", "schema": _api_schema(schema)}
    else:
        body = _plain_prompt("（system を参照）", prompt, schema)
    # 長い system は毎回同じなのでキャッシュする（同じ実行の続く記事・作り直しで安くなる）。temperature は送らない（非対応）
    kwargs = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system_instruction, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": body}],
        "output_config": output_config,
    }
    with _get_api_client().messages.stream(**kwargs) as stream:
        return stream.get_final_message()


def _try_anthropic_api(system_instruction: str, prompt: str, schema: dict, max_output_tokens: int, purpose: str) -> tuple[dict, str] | None:
    """Claude API で1回作る。使えない・失敗したら None（呼び出し側が今までの経路へ進む）。例外は外に出さない"""
    global _api_off, _api_transient_failures
    model, why = api_choose(purpose)
    if not model:
        if (purpose, why) not in _limit_logged:  # 同じ理由は1回だけ表示
            _limit_logged.add((purpose, why))
            log(f"   [llm] Claude API を使わない（{why}） -> 今までの経路へ")
        return None
    name = f"{API_PREFIX}{model}"
    # 考える分（adaptive thinking）も max_tokens に入るので余裕を持たせる（料金は実際に使った分だけ）
    max_tokens = min(64000, max(16000, max_output_tokens + 16000))
    use_schema = True
    body = prompt
    repaired = False
    for _ in range(3):
        try:
            message = _api_request(model, system_instruction, body, schema, max_tokens, use_schema)
        except ImportError:
            _api_off = "anthropic パッケージが無い"
            log(f"   [llm] Claude API: {_api_off} -> 今までの経路へ")
            return None
        except Exception as error:  # noqa: BLE001 - どんな失敗でも今までの経路へ
            kind = _api_error_kind(error)
            detail = f"{type(error).__name__}: {str(error)[:160]}"
            if kind == "exhausted":
                until = mark_api_exhausted(detail)
                _api_off = "クレジット切れ"
                log(f"   [llm] Claude API: クレジット切れ（{detail}）。{until} まで使わない -> 今までの経路へ")
                return None
            if kind == "auth":
                _api_off = "認証できない（キーを確認）"
            elif kind == "missing":
                _api_off = f"モデル {model} が見つからない"
            elif kind == "invalid" and use_schema:
                log(f"   [llm] Claude API: 400（{detail}） -> 構造化出力なしで1回だけ再試行")
                use_schema = False
                continue
            elif kind == "transient":
                _api_transient_failures += 1
                if _api_transient_failures >= 2:
                    _api_off = "一時的なエラーが続いた"
            log(f"   [llm] Claude API: 失敗（{detail}）{'。この実行ではもう使わない' if _api_off else ''} -> 今までの経路へ")
            return None
        _api_transient_failures = 0
        usd = record_api_usage(purpose, model, message.usage)
        _record_usage(name)
        usage = message.usage
        log(f"   [llm] Claude API {model}（{purpose}）: 入力 {getattr(usage, 'input_tokens', 0)}"
            f"＋キャッシュ {getattr(usage, 'cache_read_input_tokens', 0) or 0} / 出力 {getattr(usage, 'output_tokens', 0)} → ${usd:.4f}")
        if message.stop_reason in ("refusal", "max_tokens"):
            log(f"   [llm] Claude API: stop_reason={message.stop_reason} -> 今までの経路へ")
            return None
        text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text").strip()
        try:
            data = parse_json_lenient(text)
        except (ValueError, json.JSONDecodeError) as error:
            log(f"   [llm] Claude API: JSON を読めません（{str(error)[:80]}） -> 今までの経路へ")
            return None
        missing = missing_keys(data, schema)
        if missing and not repaired:
            # 1回だけ、足りないキーを伝えて出し直してもらう
            repaired = True
            log(f"   [llm] Claude API: 必須キー不足（{', '.join(missing[:6])}） -> 足りないキーを伝えて1回だけ出し直す")
            body = (prompt + "\n\n# 前回の出力の問題\n必須キーが足りませんでした: " + ", ".join(missing)
                    + "\nすべての必須キー（" + ", ".join(schema.get("required", [])) + "）を含む JSON オブジェクトを、最初から全部出力してください。")
            continue
        if missing:
            log(f"   [llm] Claude API: 必須キー不足（{', '.join(missing[:6])}） -> 今までの経路へ")
            return None
        return data, name
    return None


def api_available(purpose: str) -> bool:
    """この段で API を使える見込みがあるか（配分で無料モデルになっている段は False。回数は記録しない）"""
    if api_block_reason(purpose) is not None:
        return False
    return api_plan()["levels"].get(purpose, 0) > 0


def generate_json(
    system_instruction: str,
    prompt: str,
    schema: dict,
    *,
    temperature: float = 0.6,
    max_output_tokens: int = 16384,
    tier: str = "quality",
    visibility: str | None = None,
    purpose: str | None = None,
) -> tuple[dict, str]:
    """候補モデルを順に試し、必須キーがそろった JSON を返す。(データ, 使ったモデル名)

    purpose（API_TIERS の段）を付けると、まず Claude API を予算の配分に従ったモデルで試す（api_choose）。
    だめなら、速報・SNS・動画（API_CLI_PURPOSES か visibility="high"）は Claude CLI、そのあと tier の無料モデル。
    purpose なしは tier の無料モデルだけ（Claude は使わない）。
    環境変数 LLM_DEFAULT_VISIBILITY / LLM_DEFAULT_PURPOSE で、引数を省いた呼び出しの既定を変えられる（動画・SNS のジョブ用）。
    """
    visibility = visibility or os.environ.get("LLM_DEFAULT_VISIBILITY") or None
    purpose = purpose or os.environ.get("LLM_DEFAULT_PURPOSE") or ""
    if purpose:
        got = _try_anthropic_api(system_instruction, prompt, schema, max_output_tokens, purpose)
        if got:
            return got
    # Claude CLI（サブスクリプション）は速報・SNS・動画だけ（API が使えない・失敗したときの次の手）
    cli = visibility == "high" or purpose in API_CLI_PURPOSES
    candidates = (_claude_models() if cli else []) + [m for m in models_for(tier) if not m.startswith("claude:")]
    last_error: Exception | None = None
    used = usage_today()
    for model in candidates:
        if model in _exhausted:
            continue
        if tier == "write" and recently_slow(model):
            if (model, "slow") not in _limit_logged:
                _limit_logged.add((model, "slow"))
                log(f"   [llm] {model}: 直近{int(SLOW_COOLDOWN.total_seconds() // 60)}分以内に時間切れになったので、本文の段では飛ばす")
            continue
        if over_workflow_share(model):
            if (model, "share") not in _limit_logged:
                _limit_logged.add((model, "share"))
                log(f"   [llm] {model}: このジョブ（{current_workflow()}）の今日の取り分 {workflow_share_cap(model)} 回を使ったので飛ばす"
                    "（記事・速報の分を残す）")
            continue
        if over_daily_limit(model, tier, used):
            if (model, tier) not in _limit_logged:
                _limit_logged.add((model, tier))
                reserve = f"・速報用に{WRITE_RESERVE}回残す" if tier == "write" else ""
                log(f"   [llm] {model}: 今日の無料枠（{daily_limit(model)}回{reserve}）を使ったので飛ばす")
            if tier != "write":
                _exhausted.add(model)  # 本文の段の予備は、速報（quality）がまだ使える
            continue
        if model.startswith("claude:") and purpose != "breaking" and claude_calls_today() >= _claude_daily_max():
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
                    text = _call_gemini(model, system_instruction, prompt, schema, temperature, max_output_tokens, use_schema,
                                        timeout_ms=WRITE_TIMEOUT_MS if tier == "write" else None)
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
                slow = any(w in text.lower() for w in ("deadline", "timed out", "timeout"))
                if kind == "transient" and tier == "write" and (slow or attempt > 1):
                    # 本文の段: 時間切れ（Flash の混雑時は5分かかる・504 になる）は待たずに次の強いモデルへ。
                    # 時間切れのモデルはこの実行ではもう使わない（次の記事でまた数分待たない）。混雑（503）の再試行は1回まで
                    if slow:
                        _exhausted.add(model)
                        mark_slow(model)
                    log(f"   [llm] {model}: {'時間切れ（この実行ではもう使わない）' if slow else '混雑'}（{text[:60]}） -> 次のモデルへ")
                    break
                if kind == "transient" and attempt <= TRANSIENT_RETRIES:
                    wait = min(20, 5 * attempt) + random.uniform(0, 2)
                    log(f"   [llm] {model}: 一時的なエラー（{text[:80]}） -> {wait:.0f}s 後に再試行")
                    time.sleep(wait)
                    continue
                log(f"   [llm] {model}: 失敗（{text[:120]}） -> 次のモデルへ")
                break
    raise RuntimeError(f"すべてのモデルで失敗しました: {last_error}")


def all_exhausted(tier: str, visibility: str | None = None, purpose: str = "breaking") -> bool:
    """もう何も作れないか。Claude API が使えるうちは尽きていない扱い（記事も事実確認も API で書ける）。
    visibility="high" なら Claude CLI が使えるうちも尽きていない扱い。どちらも無ければ tier の無料モデルがすべて尽きたか"""
    if api_block_reason("check") is None:
        return False
    if visibility == "high":
        if api_available(purpose) or (purpose in API_CLI_PURPOSES and any(m not in _exhausted for m in _claude_models())):
            return False
    return all(m in _exhausted for m in models_for(tier))


# 他のスクリプトから llm.generate(...) とも呼べるように
generate = generate_json


def _load_env_local() -> None:
    """ローカル実行用: .env.local の GEMINI_API_KEY などを環境変数へ（既に設定済みなら触らない）"""
    path = ROOT / ".env.local"
    if not path.exists():
        return
    for line in path.read_text("utf-8").splitlines():
        match = re.match(r"^(GEMINI_API_KEY|GOOGLE_API_KEY|GROQ_API_KEY|OPENROUTER_API_KEY|ANTHROPIC_API_KEY)=(.+)$", line.strip())
        if match and not os.environ.get(match.group(1)):
            os.environ[match.group(1)] = match.group(2).strip().strip("\"'")


def _main_merge(argv: list[str]) -> None:
    mine_path, dest_path = Path(argv[0]), Path(argv[1])
    merged = merge_api_usage(_read_json(mine_path), _read_json(dest_path))
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    log(f"[llm] API の使用量を {dest_path} に足しました")


def _api_ping() -> None:
    """Haiku に小さな依頼を1回だけ送る（約20トークン。キーの値は表示しない）"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("[llm] ANTHROPIC_API_KEY が無いので API の確認はしません")
        return
    model = "claude-haiku-5-5"
    try:
        message = _get_api_client().messages.create(
            model=model, max_tokens=64, output_config={"effort": "low"},
            messages=[{"role": "user", "content": "「OK」とだけ答えて。"}],
        )
    except Exception as error:  # noqa: BLE001
        log(f"[llm] API の確認に失敗: {type(error).__name__}（{_api_error_kind(error)}）: {str(error)[:160]}")
        return
    usd = record_api_usage("ping", model, message.usage)
    text = "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text").strip()
    log(f"[llm] API の確認 OK: {model} → {text[:40]!r} / 入力 {message.usage.input_tokens} 出力 {message.usage.output_tokens} → ${usd:.6f}")


def _main_merge_counts(argv: list[str]) -> None:
    """ジョブごとの無料モデルの回数（{日: {ジョブ: {モデル: 回数}}}）を、モデルごとに多い方で合わせる"""
    mine_path, dest_path = Path(argv[0]), Path(argv[1])
    merged = _read_json(dest_path)
    for day, jobs in _read_json(mine_path).items():
        for job, models in (jobs or {}).items():
            target = merged.setdefault(day, {}).setdefault(job, {})
            for model, count in (models or {}).items():
                target[model] = max(int(count or 0), int(target.get(model) or 0))
    merged = {d: merged[d] for d in sorted(merged)[-3:]}
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")


if __name__ == "__main__":
    if "--merge-usage-counts" in sys.argv:
        _main_merge_counts(sys.argv[sys.argv.index("--merge-usage-counts") + 1:])
        sys.exit(0)
    if "--merge-api-usage" in sys.argv:
        _main_merge(sys.argv[sys.argv.index("--merge-api-usage") + 1:])
        sys.exit(0)
    _load_env_local()
    if "--api-ping" in sys.argv:
        _api_ping()
        sys.exit(0)
    for tier_name in ("quality", "bulk"):
        models_for(tier_name)
    log(f"[llm] 今日の使用回数: {usage_today()}")
    log(f"[llm] Claude CLI: {'有効' if claude_enabled() else '無効'}（visibility=high のみ。今日 {claude_calls_today()} 回 / 上限 {_claude_daily_max()}）")
    log(f"[llm] Claude API: {'キーあり' if os.environ.get('ANTHROPIC_API_KEY') else 'キーなし'}")
    log(api_summary_line())
    if "--ping" in sys.argv:
        demo_schema = {"type": "OBJECT", "properties": {"answer": {"type": "STRING"}, "n": {"type": "INTEGER"}}, "required": ["answer", "n"]}
        result, used = generate_json("日本語で簡潔に答える。", "1+1は？ answer に一言の説明、n に数値。", demo_schema, tier=os.environ.get("LLM_PING_TIER", "bulk"), max_output_tokens=256)
        log(f"[llm] {used}: {result}")
