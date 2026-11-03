"""Buffer API の呼び出し回数の見張り（全スクリプト共通。threads_post.buffer_graphql から必ず通る）

2026-11-03 に Buffer の 24時間の上限（HTTP 429 RATE_LIMIT_EXCEEDED, window=24h）に当たり、予約・確認・動画の差し替えが
全部止まった。原因は、空き枠が無い回でも毎回 Buffer に問い合わせていたこと（反応の学習・チャンネルの確認・見張り）。

ここで守ること:
- 1日（UTC）の呼び出し回数を数え、上限 BUFFER_DAILY_CAP（既定 250）を超えたら呼ばない。
  見張り・学習などの「読むだけ」（role=monitor）は BUFFER_MONITOR_CAP（既定 80）まで。予約（role=book）を優先する
- Buffer の応答ヘッダー（RateLimit / RateLimit-Policy）で残り回数を覚え、残りが少なければ読むだけの呼び出しを止める
- 429 を受けたら Retry-After（秒）の時刻まで一切呼ばない（ループで再試行しない）。429 自体は Buffer の上限に数えられない

Buffer の公式の上限（https://developers.buffer.com/guides/api-limits.html、2026-11-03 確認）:
  窓は 15分・24時間・30日の3つ。プランで違う。無料プランは 150回/15分・600回/24時間・1チャンネル1,000回/30日（最大3,000）。
  API キーと MCP の接続で1つの枠を共有する。429 の本文に extensions.code=RATE_LIMIT_EXCEEDED と extensions.window。
  待つ秒数は Retry-After ヘッダー（本文には無い）。成功した応答は RateLimit: r=残り, t=リセットまでの秒。

記録は state/buffer_calls/<書き手>.json（書き手ごとに別ファイル。複数のワークフローが同時に push しても衝突しない）。
書き手の名前は環境変数 BUFFER_LEDGER（ワークフローで設定）。未設定（手元・テスト）のときはファイルを読み書きせず、
そのプロセスの中だけで数える。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "state" / "buffer_calls"

ROLES = ("book", "monitor")
# 429 で Retry-After が無いときの待ち時間（窓ごと）
DEFAULT_WAIT = {"15m": 900, "24h": 3 * 3600, "30d": 12 * 3600}
MAX_WAIT = 7 * 24 * 3600


class BufferUnavailable(RuntimeError):
    """今は Buffer を呼ばない（上限・待ち時間）。RuntimeError の仲間なので、今までの except RuntimeError でも拾える"""


class BufferRateLimited(BufferUnavailable):
    pass


class BufferBudgetExceeded(BufferUnavailable):
    pass


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default


def daily_cap() -> int:
    return _int_env("BUFFER_DAILY_CAP", 250)


def monitor_cap() -> int:
    return _int_env("BUFFER_MONITOR_CAP", 80)


def writer() -> str:
    name = re.sub(r"[^A-Za-z0-9_-]", "-", os.environ.get("BUFFER_LEDGER", "").strip())[:40]
    return name


def persist() -> bool:
    return bool(writer())


_role = os.environ.get("BUFFER_ROLE", "book").strip() if os.environ.get("BUFFER_ROLE", "").strip() in ROLES else "book"
_memory: dict = {}


def set_role(role: str) -> None:
    """このプロセスの読むだけの呼び出しの扱い（monitor: 見張り・学習・点検。book: 予約に要る読み取り）"""
    global _role
    _role = role if role in ROLES else "book"


def current_role(write: bool = False) -> str:
    return "book" if write else _role


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(timezone.utc)


def _day(now: datetime) -> str:
    return now.astimezone(timezone.utc).date().isoformat()


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _empty(now: datetime) -> dict:
    return {"day": _day(now), "calls": {r: 0 for r in ROLES}, "blockedUntil": None, "windows": {}}


def _path() -> Path:
    return LEDGER_DIR / f"{writer()}.json"


def _load_mine(now: datetime) -> dict:
    data = None
    if persist():
        try:
            data = json.loads(_path().read_text("utf-8"))
        except (OSError, ValueError):
            data = None
    else:
        data = _memory.get("mine")
    if not isinstance(data, dict):
        data = _empty(now)
    if data.get("day") != _day(now):
        # 日が変わったら回数だけ0に戻す（待ち時間と残り回数の記録は残す）
        data = {**_empty(now), "blockedUntil": data.get("blockedUntil"), "windows": data.get("windows") or {}, "last429": data.get("last429")}
    data.setdefault("calls", {r: 0 for r in ROLES})
    data.setdefault("windows", {})
    return data


def _save_mine(data: dict) -> None:
    if not persist():
        _memory["mine"] = data
        return
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        _memory["mine"] = data


def _all(now: datetime) -> list[dict]:
    """全部の書き手の記録（自分の分は最新のもの）"""
    out = []
    if persist() and LEDGER_DIR.is_dir():
        mine = _path().name
        for path in sorted(LEDGER_DIR.glob("*.json")):
            if path.name == mine:
                continue
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                out.append(data)
    out.append(_load_mine(now))
    return out


def status(now: datetime | None = None) -> dict:
    """今日（UTC）の回数の合計・いつまで待つか・Buffer が最後に教えた残り回数"""
    now = _now(now)
    calls = {r: 0 for r in ROLES}
    blocked: datetime | None = None
    windows: dict = {}
    for data in _all(now):
        if data.get("day") == _day(now):
            for role in ROLES:
                calls[role] += int((data.get("calls") or {}).get(role, 0) or 0)
        until = _parse_time(data.get("blockedUntil"))
        if until and (blocked is None or until > blocked):
            blocked = until
        for w, info in (data.get("windows") or {}).items():
            if not isinstance(info, dict):
                continue
            if w not in windows or str(info.get("at", "")) > str(windows[w].get("at", "")):
                windows[w] = info
    return {"calls": calls, "total": sum(calls.values()), "blockedUntil": blocked if blocked and blocked > now else None, "windows": windows}


def _live_windows(windows: dict, now: datetime) -> list[dict]:
    """まだリセットされていない窓の、最後に分かった残り回数"""
    out = []
    for w, info in windows.items():
        at = _parse_time(info.get("at"))
        try:
            remaining, reset = int(info.get("r")), int(info.get("t"))
        except (TypeError, ValueError):
            continue
        if at and at + timedelta(seconds=reset) > now:
            out.append({"w": w, "r": remaining, "q": info.get("q"), "resetAt": at + timedelta(seconds=reset)})
    return out


def check(role: str = "book", now: datetime | None = None) -> None:
    """呼んでよいか。だめなら BufferUnavailable を投げる（Buffer には何も送らない）"""
    now = _now(now)
    s = status(now)
    if s["blockedUntil"]:
        raise BufferRateLimited(f"Buffer の上限で {s['blockedUntil'].isoformat(timespec='minutes')} まで呼び出しを止めています")
    if s["total"] >= daily_cap():
        raise BufferBudgetExceeded(f"Buffer の呼び出しが今日の上限 {daily_cap()} 回に達しました（UTC の日付が変わるまで止めます）")
    if role != "book" and s["calls"]["monitor"] >= monitor_cap():
        raise BufferBudgetExceeded(f"読むだけの Buffer 呼び出しが今日の上限 {monitor_cap()} 回に達しました（予約の分を残すため）")
    for win in _live_windows(s["windows"], now):
        quota = int(win["q"]) if str(win.get("q") or "").isdigit() else 0
        reserve = max(20, quota // 4) if role != "book" else 3
        if win["r"] <= reserve:
            raise BufferBudgetExceeded(
                f"Buffer の残り回数が少ない（窓 {win['w']}秒: 残り {win['r']}回、{win['resetAt'].isoformat(timespec='minutes')} にリセット）"
            )


_ITEM = re.compile(r'\s*(?:"([^"]*)"|([^;,\s]+))((?:\s*;\s*[A-Za-z]+=[^;,]*)*)')


def _parse_items(value: str) -> list[tuple[str, dict]]:
    out = []
    for part in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", value or ""):
        m = _ITEM.match(part)
        if not m:
            continue
        name = m.group(1) if m.group(1) is not None else m.group(2)
        params = {}
        for kv in (m.group(3) or "").split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k.strip()] = v.strip().strip('"')
        out.append((name, params))
    return out


def parse_headers(headers) -> dict:
    """RateLimit / RateLimit-Policy ヘッダーから {窓の秒数: {r, t, q}}。窓は w（900/86400/2592000）で見分ける"""
    get = (lambda k: headers.get(k)) if headers is not None else (lambda k: None)
    policies = {name: p for name, p in _parse_items(get("RateLimit-Policy") or "")}
    out: dict = {}
    for name, p in _parse_items(get("RateLimit") or ""):
        if "r" not in p:
            continue
        pol = policies.get(name, {})
        w = pol.get("w") or name
        entry = {"r": p.get("r"), "t": p.get("t", "0")}
        if pol.get("q"):
            entry["q"] = pol["q"]
        out[str(w)] = entry
    return out


def record(role: str = "book", headers=None, now: datetime | None = None) -> None:
    """Buffer に1回送った（成功・失敗とも数える。429 だけは record_429）"""
    now = _now(now)
    data = _load_mine(now)
    data["calls"][role if role in ROLES else "book"] = int(data["calls"].get(role, 0) or 0) + 1
    for w, entry in parse_headers(headers).items():
        data["windows"][w] = {**entry, "at": now.isoformat(timespec="seconds")}
    _save_mine(data)


def record_429(headers=None, body: str = "", now: datetime | None = None) -> datetime:
    """429 を受けた。Retry-After の秒数（無ければ窓ごとの既定）だけ、全部の呼び出しを止める。止める時刻を返す"""
    now = _now(now)
    window = ""
    m = re.search(r'"window"\s*:\s*"([^"]+)"', body or "")
    if m:
        window = m.group(1)
    wait = None
    raw = headers.get("Retry-After") if headers is not None else None
    if raw:
        try:
            wait = int(float(raw))
        except ValueError:
            when = None
            try:
                from email.utils import parsedate_to_datetime

                when = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                when = None
            if when:
                wait = int((when - now).total_seconds())
    if wait is None or wait <= 0:
        wait = DEFAULT_WAIT.get(window, 3600)
    until = now + timedelta(seconds=min(wait, MAX_WAIT))
    data = _load_mine(now)
    old = _parse_time(data.get("blockedUntil"))
    data["blockedUntil"] = max(until, old).isoformat(timespec="seconds") if old else until.isoformat(timespec="seconds")
    data["last429"] = {"at": now.isoformat(timespec="seconds"), "window": window, "retryAfter": raw or ""}
    _save_mine(data)
    return until


def reset_memory() -> None:
    """テスト用"""
    _memory.clear()
    set_role(os.environ.get("BUFFER_ROLE", "book"))
