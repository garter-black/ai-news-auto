#!/usr/bin/env python3
"""速報レーン: 主要企業の公式発表を、見張り番（Cloudflare Worker）が数分おきに見つけて記事作りに直行させる仕組みの Python 側。

- 判定のルールは scripts/fast_lane_rules.json（Worker の ops/watchdog/src/fastlane.js と同じ判定）
- 処理結果は state/fast_lane.json に残す。Worker はこれを読んで「受け取った」と判断し、再起動をやめる
  （outcome: created / updated / duplicate / skipped / retry / failed / capped）
- レーダー（Worker の src/radar.js・scripts/radar.py）が見つけた話題も同じ速報レーンで作る。受け付ける URL は
  対象の発信元・レーダーの一次情報（公式・SEC）・信頼できる報道機関（rules の radar）。配信サービスは受け付けない
- daily_max（30）は速報レーン＋レーダーの1日の安全上限（暴走よけ）。通常の記事の上限（WEB_DAILY_CAP）とは別

  python3 scripts/fast_lane.py --check              # いまの公式フィードで、速報レーンに乗る見出しを表示（API 不要）
  python3 scripts/fast_lane.py --check --days 14    # 過去14日分で判定の様子を見る（ルールの調整用）
  python3 scripts/fast_lane.py --verify-registry     # 追加の発信元（sources_registry.json）が読めるか（robots.txt も見る）
  echo '[{"title": "...", "source": "openai-news"}]' | python3 scripts/fast_lane.py --classify   # Worker のテスト用
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = Path(__file__).resolve().with_name("fast_lane_rules.json")
REGISTRY_PATH = Path(__file__).resolve().with_name("sources_registry.json")
ACK_PATH = ROOT / "state" / "fast_lane.json"
ACK_KEEP_DAYS = 14
JST = timezone(timedelta(hours=9))


def merge_registry(data: dict, registry: dict) -> dict:
    """sources_registry.json（追加の発信元）を sources に足す（Worker の mergeRegistry と同じ。同じ slug は rules 側を優先）"""
    have = {s["slug"] for s in data["sources"]}
    extra = [{**s, "registry": True} for s in registry.get("sources") or [] if s["slug"] not in have]
    data["sources"] = [*data["sources"], *extra]
    data["registry"] = {k: v for k, v in registry.items() if k != "sources"}
    return data


@lru_cache(maxsize=1)
def rules() -> dict:
    data = json.loads(RULES_PATH.read_text("utf-8"))
    if REGISTRY_PATH.exists():
        merge_registry(data, json.loads(REGISTRY_PATH.read_text("utf-8")))
    flags = re.IGNORECASE
    reg = data.get("registry") or {}
    data["_re"] = {
        "model": re.compile(data["model_re"], flags),
        "head": re.compile(data["head_re"], flags),
        "launch": re.compile(data["launch_re"], flags),
        "exclude": re.compile(data["exclude_re"], flags),
        "ai": re.compile(data["ai_re"], flags),
        # 追加の発信元（tier B・C）の強い語・新モデル
        "strong": re.compile(reg["strong_re"], flags) if reg.get("strong_re") else None,
        "reg_model": re.compile(reg["model_re"], flags) if reg.get("model_re") else None,
    }
    data["_by_slug"] = {s["slug"]: s for s in data["sources"]}
    # 発信元ごとの除外語・必須語
    data["_src_re"] = {
        s["slug"]: {
            "exclude": re.compile(s["exclude_re"], flags) if s.get("exclude_re") else None,
            "include": re.compile(s["include_re"], flags) if s.get("include_re") else None,
        }
        for s in data["sources"] if s.get("exclude_re") or s.get("include_re")
    }
    # ジャンル（新製品・ゲーム・ビジネス）ごとの判定
    data["_genre_re"] = {
        k: {
            "major": re.compile(g["major_re"], flags), "exclude": re.compile(g["exclude_re"], flags),
            "own": bool(g.get("own_exclude")), "reason": g.get("reason") or "新製品・ゲームの発表",
        }
        for k, g in (data.get("genres") or {}).items() if not k.startswith("_")
    }
    return data


WAIT_REASON = "話題待ち（強い語なし）"  # fastlane.js の WAIT_REASON と同じ


def genre_of(slug: str) -> str:
    """発信元のジャンル（無ければ ai）"""
    return (source(slug) or {}).get("genre") or "ai"


def source(slug: str) -> dict | None:
    return rules()["_by_slug"].get(slug or "")


def relevant(title: str, slug: str) -> tuple[bool, str]:
    """速報レーンに乗せるか。(結果, 理由)。理由の文言は fastlane.js と同じ（テストで突き合わせる）"""
    r = rules()
    src = source(slug)
    if not src:
        return False, "対象外の発信元"
    t = str(title or "").strip()
    g = r["_genre_re"].get(src.get("genre") or "")
    if not (g and g["own"]):
        ex = r["_re"]["exclude"].search(t)
        if ex:
            return False, f"除外語（{ex.group(0)}）"
    own = r["_src_re"].get(slug) or {}
    ox = own.get("exclude") and own["exclude"].search(t)
    if ox:
        return False, f"除外語（{ox.group(0)}）"
    if own.get("include") and not own["include"].search(t):
        return False, "必須の語がない"
    if g:
        # 新製品・ゲーム・ビジネスの公式: 細かいお知らせを除き、大きな発表の語があるものだけ
        gx = g["exclude"].search(t)
        if gx:
            return False, f"除外語（{gx.group(0)}）"
        if r["_re"]["model"].search(t) and (r["_re"]["launch"].search(t) or r["_re"]["head"].search(t)):
            return True, "新モデル・新製品"
        if g["major"].search(t):
            return True, g["reason"]
        return False, "大きな発表の語がない"
    model = bool(r["_re"]["model"].search(t))
    if src.get("ai_only") and not model and not r["_re"]["ai"].search(t):
        return False, "AI の話題ではない"
    launch = bool(r["_re"]["launch"].search(t))
    if src.get("tier") in ("B", "C"):
        # tier B・C: 強い語・新モデルだけすぐ。発表の語だけなら、まとめサイトで話題になるのを待つ（Worker の pending）
        if r["_re"]["strong"] and r["_re"]["strong"].search(t):
            return True, "大きな発表"
        if r["_re"]["reg_model"] and r["_re"]["reg_model"].search(t) and (launch or r["_re"]["head"].search(t)):
            return True, "新モデル・新製品"
        if launch:
            return False, WAIT_REASON
        return False, "発表の語がない"
    if model and (launch or r["_re"]["head"].search(t)):
        return True, "新モデル・新製品"
    if launch:
        # 対象は各社の公式ニュースルームだけなので、発表の語があれば自社（か提携先と共同）の発表とみなす
        return True, "公式の発表"
    return False, "発表の語がない"


def _host_in(host: str, base: str) -> bool:
    base = base.lower().removeprefix("www.")
    return host == base or host.endswith("." + base)


def allowed_url(url: str) -> bool:
    """速報レーンで受け付ける URL か（https で、対象の発信元・レーダーの一次情報（公式・SEC）・信頼できる報道機関のサイト。配信サービスは除く）"""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.netloc:
        return False
    host = parts.netloc.lower().split(":")[0]
    radar = rules().get("radar") or {}
    if any(_host_in(host, b) for b in radar.get("blocked_hosts") or []):
        return False
    bases = [urllib.parse.urlsplit(src["url"]).netloc for src in rules()["sources"]]
    bases += [h["host"] for h in radar.get("primary_hosts") or []] + [o["host"] for o in radar.get("outlets") or []]
    return any(_host_in(host, base) for base in bases)


def outlet(url: str) -> dict | None:
    """信頼できる報道機関（レーダーの outlets）の URL なら {host, name}。公式の一次情報が無いときだけ出典にする"""
    try:
        host = urllib.parse.urlsplit(url.strip()).netloc.lower()
    except ValueError:
        return None
    for o in (rules().get("radar") or {}).get("outlets") or []:
        if _host_in(host, o["host"]):
            return o
    return None


def primary_name(url: str) -> str:
    """レーダーの一次情報（公式・SEC）の URL なら発信元の名前"""
    try:
        host = urllib.parse.urlsplit(url.strip()).netloc.lower()
    except ValueError:
        return ""
    for h in (rules().get("radar") or {}).get("primary_hosts") or []:
        if _host_in(host, h["host"]):
            return h["name"]
    return ""


def register_sources(feeds_module) -> None:
    """RSS の無い発信元（Anthropic など）と、レーダーの出典（公式・報道機関）を feeds.FEEDS_BY_SLUG に足す
    （source_rank・発信元名を引けるように）。FEEDS には入れないので、通常の収集（collect）では読まない"""
    for src in rules()["sources"]:
        if src["slug"] in feeds_module.FEEDS_BY_SLUG:
            continue
        feeds_module.FEEDS_BY_SLUG[src["slug"]] = {
            "slug": src["slug"], "name": src["name"], "url": src["url"], "lang": src.get("lang") or "en", "priority": 1 if src.get("tier", "A") == "A" else 2,
            "kind": "official", "primary_for": src.get("entity"), "fast_only": True,
        }
    radar = rules().get("radar") or {}
    feeds_module.FEEDS_BY_SLUG.setdefault("radar-official", {
        "slug": "radar-official", "name": "公式発表", "url": "", "lang": "en", "priority": 1, "kind": "official", "fast_only": True,
    })
    for o in radar.get("outlets") or []:
        slug = f"radar-{o['host']}"
        feeds_module.FEEDS_BY_SLUG.setdefault(slug, {
            "slug": slug, "name": o["name"], "url": f"https://{o['host']}/", "lang": "ja" if o["host"].endswith(".jp") or o["host"] == "gori.me" else "en",
            "priority": 2, "kind": "press", "fast_only": True,
        })


# --------------------------------------------------------------------------- #
# 処理結果（Worker への受領の知らせ）
# --------------------------------------------------------------------------- #


def load_acks() -> dict:
    try:
        data = json.loads(ACK_PATH.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        data = {}
    data.setdefault("items", {})
    return data


def save_acks(data: dict) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=ACK_KEEP_DAYS)
    items = {}
    for key, entry in data.get("items", {}).items():
        try:
            if datetime.fromisoformat(entry.get("finishedAt") or entry.get("startedAt")) < since:
                continue
        except (TypeError, ValueError):
            pass
        items[key] = entry
    ACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACK_PATH.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")


def created_today(data: dict, now: datetime | None = None) -> int:
    """今日（日本時間）速報レーンで新しく記事にした本数（パイプライン側の上限に使う）"""
    today = (now or datetime.now(timezone.utc)).astimezone(JST).date()
    count = 0
    for entry in data.get("items", {}).values():
        # 地震・津波（quake.py）は安全のため全体の上限に数えない
        if entry.get("outcome") != "created" or entry.get("lane") == "disaster":
            continue
        try:
            if datetime.fromisoformat(entry["finishedAt"]).astimezone(JST).date() == today:
                count += 1
        except (KeyError, TypeError, ValueError):
            continue
    return count


# --------------------------------------------------------------------------- #
# 通常の回が速報レーンに譲るための確認
# --------------------------------------------------------------------------- #

RUN_PREFIX = "速報レーン"  # auto_run.yml の run-name と Worker の RUN_PREFIX と同じ
WAITING = {"queued", "pending", "waiting", "requested"}
_waiting_cache = {"at": -1e9, "value": False}


def fast_waiting(min_interval: float = 45.0) -> bool:
    """速報レーンの実行が待機中か。GitHub Actions の中（FAST_LANE_GH_TOKEN と GITHUB_REPOSITORY がある）でだけ確かめる。
    確認は min_interval 秒に1回まで。失敗したら False（通常の回をそのまま続ける）"""
    token = os.environ.get("FAST_LANE_GH_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        return False
    now = time.monotonic()
    if now - _waiting_cache["at"] < min_interval:
        return _waiting_cache["value"]
    _waiting_cache["at"] = now
    value = False
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/actions/workflows/auto_run.yml/runs?per_page=10",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "saisoku-pipeline"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            runs = json.load(response).get("workflow_runs") or []
        value = any(r.get("status") in WAITING and str(r.get("display_title", "")).startswith(RUN_PREFIX) for r in runs)
    except Exception:  # noqa: BLE001
        value = False
    _waiting_cache["value"] = value
    return value


def parse_meta(raw: str) -> list[dict]:
    """Worker が渡す fast_meta（JSON の配列か1件のオブジェクト）。壊れていれば空"""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    items = [d for d in data if isinstance(d, dict) and isinstance(d.get("url"), str)]
    for d in items:
        # Worker の古い版が戻しそこねた実体参照（&#8211; など）が見出しに残らないように
        for key in ("title", "sourceName"):
            if isinstance(d.get(key), str):
                d[key] = _unescape(d[key])
    return items


def _unescape(value: str) -> str:
    out = value
    for _ in range(3):
        nxt = html.unescape(out)
        if nxt == out:
            break
        out = nxt
    return out


def parse_time(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def missed_genres(hours: int) -> int:
    """genre のある発信元（新製品・ゲーム）の直近 hours 時間の見出しのうち、速報レーンの対象なのに記事が無いもの。
    記事の有無は出典 URL（sourceUrl・続報の sourceUrl）で見る。LLM は使わない"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import feeds  # noqa: PLC0415

    have = set()
    for path in (ROOT / "content" / "articles").glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except ValueError:
            continue
        for url in [data.get("sourceUrl"), *(u.get("sourceUrl") for u in data.get("updates") or [])]:
            if url:
                have.add(feeds.item_id(url))
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    lines = ["### 新製品・ゲーム・ビジネスの公式発表", ""]
    missing = 0
    for src in rules()["sources"]:
        if not src.get("genre") or src.get("type") != "rss":
            continue
        try:
            items = feeds.parse_feed({**src, "lang": "ja"})
        except Exception as error:  # noqa: BLE001
            lines.append(f"- {src['name']}: 取得失敗（{str(error)[:60]}）")
            continue
        for item in items:
            if not item["published_at"] or item["published_at"] < since:
                continue
            ok, _ = relevant(item["title"], src["slug"])
            if not ok:
                continue
            exists = item["id"] in have
            missing += not exists
            lines.append(f"- {'記事あり' if exists else '**記事なし**'}（{src['genre']}）{src['name']}: {item['title'][:70]}  {item['url']}")
    if len(lines) == 2:
        lines.append(f"- 直近{hours}時間に対象の発表はありません")
    print("\n".join(lines))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    if missing:
        print(f"::warning::新製品・ゲームの公式発表で記事が無いものが {missing} 件あります（ジョブのサマリーを確認）")
    return 0


def _have_ids(feeds) -> set:
    have = set()
    for path in (ROOT / "content" / "articles").glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except ValueError:
            continue
        for url in [data.get("sourceUrl"), *(u.get("sourceUrl") for u in data.get("updates") or [])]:
            if url:
                have.add(feeds.item_id(url))
    return have


def _summary(lines: list[str]) -> None:
    print("\n".join(lines))
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def missed_registry(hours: int) -> int:
    """追加の発信元（sources_registry.json・genre なし・RSS）の直近 hours 時間の見出しのうち、すぐ速報になる（◎）・話題待ち（△）で
    記事が無いもの。取得できない発信元も並べる（フィードの故障に気づくため）。LLM は使わない。一覧ページ（html）は Worker だけが読む"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import feeds  # noqa: PLC0415

    have = _have_ids(feeds)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    lines = ["### 追加の発信元（AI 企業・AI につながる道具）の公式発表", "", "◎=すぐ速報 / △=話題になったら速報（話題待ち）", ""]
    missing, broken = 0, []
    for src in rules()["sources"]:
        if not src.get("registry") or src.get("genre") or src.get("type") != "rss":
            continue
        try:
            items = feeds.parse_feed({**src, "lang": src.get("lang", "en")})
        except Exception as error:  # noqa: BLE001
            broken.append(f"{src['name']}（{str(error)[:50]}）")
            continue
        if not items:
            broken.append(f"{src['name']}（0件）")
        for item in items:
            if not item["published_at"] or item["published_at"] < since:
                continue
            ok, why = relevant(item["title"], src["slug"])
            if not ok and why != WAIT_REASON:
                continue
            exists = item["id"] in have
            missing += ok and not exists
            mark = "◎" if ok else "△"
            lines.append(f"- {mark} {'記事あり' if exists else '**記事なし**'}（tier {src.get('tier')}）{src['name']}: {item['title'][:70]}  {item['url']}")
    if len(lines) == 4:
        lines.append(f"- 直近{hours}時間に対象の発表はありません")
    if broken:
        lines += ["", f"取得できない発信元（{len(broken)}）: " + "、".join(broken)]
    _summary(lines)
    if missing:
        print(f"::warning::追加の発信元ですぐ速報になるはずの発表で記事が無いものが {missing} 件あります（ジョブのサマリーを確認）")
    return 0


def verify_registry() -> int:
    """sources_registry.json の発信元が読めるか（HTTP・件数・いちばん新しい記事の日付・robots.txt）。足すときに使う"""
    import urllib.robotparser  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import feeds  # noqa: PLC0415

    ua = "SaisokuFastLane"
    bad = 0
    robots: dict = {}
    for src in rules()["sources"]:
        if not src.get("registry"):
            continue
        parts = urllib.parse.urlsplit(src["url"])
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in robots:
            # robots.txt は自分の UA で読む（urllib の既定の UA だと 403 で「全部不可」になるサイトがある）
            rp = urllib.robotparser.RobotFileParser()
            try:
                req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": f"Mozilla/5.0 (compatible; {ua}/1.0; +https://ai-tech-express.jp)"})
                with urllib.request.urlopen(req, timeout=15) as res:
                    rp.parse(res.read().decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as error:
                rp.parse(["User-agent: *", "Disallow: /"] if error.code in (401, 403) else [])
            except Exception:  # noqa: BLE001
                rp.parse([])
            robots[base] = rp
        allowed = robots[base].can_fetch(ua, src["url"])
        note = ""
        try:
            if src["type"] == "rss":
                items = feeds.parse_feed({**src, "lang": src.get("lang", "en")})
                dates = [i["published_at"] for i in items if i["published_at"]]
                newest = max(dates).astimezone(JST).strftime("%Y-%m-%d") if dates else "日付なし"
                note = f"{len(items)}件・最新 {newest}"
                ok = bool(items)
            else:
                req = urllib.request.Request(src["url"], headers={"User-Agent": f"Mozilla/5.0 (compatible; {ua}/1.0; +https://ai-tech-express.jp)"})
                with urllib.request.urlopen(req, timeout=20) as res:
                    body = res.read().decode("utf-8", "replace")
                link_re = re.compile(src.get("link_re") or ".", re.IGNORECASE)
                hrefs = {urllib.parse.urlsplit(urllib.parse.urljoin(src["url"], h)) for h in re.findall(r"href=[\"']([^\"'#?]+)", body)}
                links = len({h.path for h in hrefs if h.netloc == parts.netloc and link_re.search(h.path)})
                note = f"一覧 {len(body) // 1024}KB・記事らしいリンク {links}"
                ok = links > 0
        except Exception as error:  # noqa: BLE001
            ok, note = False, str(error)[:60]
        ok = ok and allowed
        bad += not ok
        print(f"{'OK' if ok else 'NG'} [{src.get('tier')}] {src['slug']}: {note}{'' if allowed else '・robots.txt で不可'}")
    print(f"\n読めない発信元: {bad}")
    return 1 if bad else 0


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="いまの公式フィードの見出しを判定して表示")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--classify", action="store_true", help="標準入力の [{title, source}] を判定して JSON で返す")
    parser.add_argument("--missed-genres", action="store_true", help="新製品・ゲームの公式で、対象なのに記事が無いものを表示（見逃しチェック）")
    parser.add_argument("--missed-registry", action="store_true", help="追加の発信元で、速報になるはずなのに記事が無いものを表示（見逃しチェック）")
    parser.add_argument("--verify-registry", action="store_true", help="追加の発信元が読めるか確かめる")
    parser.add_argument("--hours", type=int, default=30)
    args = parser.parse_args()

    if args.missed_genres:
        return missed_genres(args.hours)
    if args.missed_registry:
        return missed_registry(args.hours)
    if args.verify_registry:
        return verify_registry()

    if args.classify:
        items = json.load(sys.stdin)
        out = [dict(zip(("ok", "reason"), relevant(i.get("title", ""), i.get("source", "")))) for i in items]
        json.dump(out, sys.stdout, ensure_ascii=False)
        return 0

    if args.check:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import feeds  # noqa: PLC0415

        since = datetime.now(timezone.utc) - timedelta(days=args.days)
        passed = 0
        for src in rules()["sources"]:
            if src["type"] != "rss":
                print(f"\n■ {src['slug']}（{src['type']}。Worker だけが読む）")
                continue
            try:
                items = feeds.parse_feed({**src, "lang": "en"})
            except Exception as error:  # noqa: BLE001
                print(f"\n■ {src['slug']}: 取得失敗 {error}")
                continue
            items = [i for i in items if i["published_at"] and i["published_at"] >= since]
            print(f"\n■ {src['slug']}: 直近{args.days}日 {len(items)} 件")
            for item in items:
                ok, why = relevant(item["title"], src["slug"])
                passed += ok
                print(f"  {'◎' if ok else '  '} {item['published_at'].astimezone(JST):%m-%d %H:%M} {item['title'][:80]}  ({why})")
        print(f"\n速報レーンに乗る見出し: {passed} 件（直近{args.days}日）")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
