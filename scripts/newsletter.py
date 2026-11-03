#!/usr/bin/env python3
"""毎朝のメール配信（前日24時間の主要記事5本）。GitHub Actions の newsletter.yml が 07:00 JST に実行する。

送信の鍵が無いときは何もしない（成功で終わる）。優先順:
  1. Cloudflare Email Service（REST）: CF_EMAIL_TOKEN + CLOUDFLARE_ACCOUNT_ID
  2. Resend: RESEND_API_KEY
購読者は Cloudflare KV（NEWSLETTER_KV。サイトの /api/newsletter/* が書く）から読む:
  NEWSLETTER_KV_TOKEN（無ければ CF_DEPLOY_TOKEN。Workers KV Storage の権限）+ CLOUDFLARE_ACCOUNT_ID

  python3 scripts/newsletter.py --dry-run   # 送らずに文面を out/newsletter/ に書き出す
  python3 scripts/newsletter.py             # 確認待ちへの確認メール → 本配信
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import JST, ROOT, article_url, pick_top, site_url, summary_lines, title_of  # noqa: E402

KV_NAMESPACE_ID = os.environ.get("NEWSLETTER_KV_ID", "d0108c8275074124b8fdfbd577111999")
FROM_ADDRESS = os.environ.get("NEWSLETTER_FROM", "news@ai-tech-express.jp")
FROM_NAME = "サイソクニュース"
# 送信の上限（Resend の無料枠は1日100通。確認メールも数える）
DAILY_MAX = int(os.environ.get("NEWSLETTER_DAILY_MAX", "95"))


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------- 送信


def provider() -> str | None:
    if os.environ.get("CF_EMAIL_TOKEN") and os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        return "cloudflare"
    if os.environ.get("RESEND_API_KEY"):
        return "resend"
    return None


def _request(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def send_mail(to: str, subject: str, html_body: str, text: str, headers: dict | None = None) -> bool:
    kind = provider()
    if kind == "cloudflare":
        account = os.environ["CLOUDFLARE_ACCOUNT_ID"]
        payload = {
            "to": to,
            "from": {"address": FROM_ADDRESS, "name": FROM_NAME},
            "subject": subject,
            "html": html_body,
            "text": text,
            "headers": headers or {},
        }
        status, body = _request(
            f"https://api.cloudflare.com/client/v4/accounts/{account}/email/sending/send",
            "POST",
            json.dumps(payload).encode(),
            {"Authorization": f"Bearer {os.environ['CF_EMAIL_TOKEN']}", "Content-Type": "application/json"},
        )
    elif kind == "resend":
        payload = {
            "from": f"{FROM_NAME} <{FROM_ADDRESS}>",
            "to": [to],
            "subject": subject,
            "html": html_body,
            "text": text,
            "headers": headers or {},
        }
        status, body = _request(
            "https://api.resend.com/emails",
            "POST",
            json.dumps(payload).encode(),
            {"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}", "Content-Type": "application/json"},
        )
    else:
        return False
    if status >= 300:
        log(f"   [NG] {kind} {status}: {body[:200].decode(errors='replace')}")
        return False
    return True


# ---------------------------------------------------------------- 購読者（KV）


def kv_base() -> str | None:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("NEWSLETTER_KV_TOKEN") or os.environ.get("CF_DEPLOY_TOKEN")
    if not account or not token:
        return None
    return f"https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{KV_NAMESPACE_ID}"


def kv_headers() -> dict:
    token = os.environ.get("NEWSLETTER_KV_TOKEN") or os.environ.get("CF_DEPLOY_TOKEN")
    return {"Authorization": f"Bearer {token}"}


def kv_list(prefix: str) -> list[dict]:
    base, keys, cursor = kv_base(), [], ""
    while True:
        query = urllib.parse.urlencode({"prefix": prefix, "limit": 1000, **({"cursor": cursor} if cursor else {})})
        status, body = _request(f"{base}/keys?{query}", headers=kv_headers())
        if status != 200:
            raise RuntimeError(f"KV list {status}: {body[:200]!r}")
        data = json.loads(body)
        keys += data.get("result", [])
        cursor = (data.get("result_info") or {}).get("cursor") or ""
        if not cursor:
            return keys


def kv_get(key: str) -> str | None:
    status, body = _request(f"{kv_base()}/values/{urllib.parse.quote(key, safe='')}", headers=kv_headers())
    return body.decode() if status == 200 else None


def kv_put(key: str, value: str, metadata: dict | None = None) -> bool:
    """値とメタデータをまとめて書く（multipart）"""
    boundary = uuid.uuid4().hex
    parts = [("value", value)]
    if metadata is not None:
        parts.append(("metadata", json.dumps(metadata)))
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{content}\r\n'.encode()
        for name, content in parts
    ) + f"--{boundary}--\r\n".encode()
    status, _ = _request(
        f"{kv_base()}/values/{urllib.parse.quote(key, safe='')}",
        "PUT",
        body,
        {**kv_headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return status == 200


# ---------------------------------------------------------------- 文面


def unsubscribe_links(sub_id: str, token: str) -> tuple[str, str]:
    q = urllib.parse.urlencode({"id": sub_id, "t": token})
    return f"{site_url()}/newsletter/unsubscribe?{q}", f"{site_url()}/api/newsletter/unsubscribe?{q}"


def build_digest(articles: list[dict], date_label: str, campaign: str) -> dict:
    items = [
        {"title": title_of(a), "lines": summary_lines(a), "url": article_url(a["slug"], "newsletter", campaign)}
        for a in articles
    ]
    lead = items[0]["title"]
    lead = lead if len(lead) <= 28 else lead[:27] + "…"
    subject = f"【サイソクニュース朝刊 {date_label}】{lead}ほか{len(items) - 1}本" if len(items) > 1 else f"【サイソクニュース朝刊 {date_label}】{lead}"
    return {"subject": subject, "items": items, "date": date_label}


def render(digest: dict, unsubscribe_url: str) -> tuple[str, str]:
    text_parts = [f"サイソクニュース 朝刊（{digest['date']}）", "昨日のAIニュースから、押さえておきたい5本です。", ""]
    for i, item in enumerate(digest["items"], 1):
        text_parts += [f"{i}. {item['title']}", *[f"   ・{line}" for line in item["lines"]], f"   {item['url']}", ""]
    text_parts += [
        "―",
        f"すべての記事: {site_url()}/?utm_source=newsletter&utm_medium=email",
        f"配信の解除: {unsubscribe_url}",
        "サイソクニュース（記事はAIが一次情報を解析して作成しています。重要な判断は公式発表をご確認ください）",
    ]
    text = "\n".join(text_parts)

    esc = html.escape
    rows = "".join(
        f"""<tr><td style="padding:18px 0;border-top:1px solid #dce2eb">
<p style="margin:0;font-size:12px;color:#465268;font-family:Menlo,Consolas,monospace">{i:02d}</p>
<a href="{esc(item['url'])}" style="display:block;margin-top:4px;font-size:16px;line-height:1.55;font-weight:700;color:#111;text-decoration:none">{esc(item['title'])}</a>
{''.join(f'<p style="margin:6px 0 0;font-size:14px;line-height:1.75;color:#2b3954">{esc(line)}</p>' for line in item['lines'])}
<a href="{esc(item['url'])}" style="display:inline-block;margin-top:8px;font-size:13px;font-weight:700;color:#1d4a8f;text-decoration:none">記事を読む →</a>
</td></tr>"""
        for i, item in enumerate(digest["items"], 1)
    )
    body = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{esc(digest['subject'])}</title></head>
<body style="margin:0;padding:0;background:#f3f5f8">
<div style="display:none;max-height:0;overflow:hidden">{esc(digest['items'][0]['title'])}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f5f8"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fff;border-top:3px solid #111;font-family:'Hiragino Sans','Hiragino Kaku Gothic ProN','Noto Sans JP',Meiryo,sans-serif;color:#111">
<tr><td style="padding:24px 24px 6px">
<p style="margin:0;font-size:12px;font-weight:700;letter-spacing:.08em;color:#465268">サイソクニュース 朝刊 ・ {esc(digest['date'])}</p>
<p style="margin:8px 0 0;font-size:15px;line-height:1.8;color:#2b3954">昨日のAIニュースから、押さえておきたい5本です。</p>
</td></tr>
<tr><td style="padding:0 24px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>
<tr><td style="padding:18px 24px 26px;border-top:1px solid #dce2eb">
<p style="margin:0;font-size:12px;line-height:1.8;color:#465268">記事はAIが一次情報（公式発表など）を解析して作成しています。重要な判断の際は公式発表をご確認ください。</p>
<p style="margin:8px 0 0;font-size:12px;line-height:1.8;color:#465268"><a href="{site_url()}/?utm_source=newsletter&utm_medium=email" style="color:#465268">サイトを開く</a> ・ <a href="{esc(unsubscribe_url)}" style="color:#465268">配信を解除する</a></p>
</td></tr></table></td></tr></table></body></html>"""
    return body, text


def confirm_mail(sub_id: str, token: str) -> tuple[str, str, str]:
    url = f"{site_url()}/api/newsletter/confirm?id={sub_id}&t={token}"
    subject = "【サイソクニュース】メール配信の登録を確認してください"
    text = (
        "サイソクニュースのメール配信にご登録いただき、ありがとうございます。\n\n"
        f"下のリンクを開くと登録が完了し、翌朝7時から配信が始まります。\n{url}\n\n"
        "お心当たりがない場合は、このメールを破棄してください。登録はされません。"
    )
    body = (
        '<!doctype html><html lang="ja"><body style="font-family:sans-serif;color:#111;line-height:1.9">'
        "<p>サイソクニュースのメール配信にご登録いただき、ありがとうございます。</p>"
        f'<p><a href="{html.escape(url)}" style="display:inline-block;background:#1d4a8f;color:#fff;padding:12px 22px;text-decoration:none;font-weight:700">登録を完了する</a></p>'
        '<p style="font-size:12px;color:#465268">お心当たりがない場合は、このメールを破棄してください。登録はされません。</p></body></html>'
    )
    return subject, body, text


# ---------------------------------------------------------------- 実行


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="送らずに文面を out/newsletter/ に書き出す")
    parser.add_argument("--force", action="store_true", help="今日すでに配信済みでも送る")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    jst = now.astimezone(JST)
    date_label = f"{jst.month}/{jst.day}"
    campaign = f"daily-{jst:%Y%m%d}"

    articles = pick_top(args.hours, args.n, now)
    if not articles:
        log("[skip] 直近の対象記事がありません")
        return 0
    digest = build_digest(articles, date_label, campaign)

    if args.dry_run:
        out = ROOT / "out" / "newsletter"
        out.mkdir(parents=True, exist_ok=True)
        body, text = render(digest, f"{site_url()}/newsletter/unsubscribe?id=SAMPLE&t=SAMPLE")
        (out / f"{jst:%Y-%m-%d}.html").write_text(body, encoding="utf-8")
        (out / f"{jst:%Y-%m-%d}.txt").write_text(f"件名: {digest['subject']}\n\n{text}\n", encoding="utf-8")
        log(f"[dry-run] 件名: {digest['subject']}\n{text}\n→ {out}")
        return 0

    if not provider():
        log("[skip] 送信の鍵（CF_EMAIL_TOKEN / RESEND_API_KEY）が未設定のため、何もしません")
        return 0
    if not kv_base():
        log("[skip] 購読者の読み出しに必要な CLOUDFLARE_ACCOUNT_ID と KV のトークンが未設定です")
        return 0

    budget = DAILY_MAX
    subs = kv_list("sub:")
    # 1) 送信の設定前に登録された人へ、確認メールをまとめて送る
    for key in subs:
        meta = key.get("metadata") or {}
        if meta.get("s") != "pending" or meta.get("c") or budget <= 0:
            continue
        sub_id = key["name"].split(":", 1)[1]
        raw = kv_get(key["name"])
        if not raw:
            continue
        sub = json.loads(raw)
        subject, body, text = confirm_mail(sub_id, sub["token"])
        if send_mail(sub["email"], subject, body, text):
            budget -= 1
            sub["confirmSentAt"] = now.isoformat()
            kv_put(key["name"], json.dumps(sub, ensure_ascii=False), {"s": "pending", "e": sub["email"], "t": sub["token"], "c": 1})
            log(f"   確認メール: {sub_id[:8]}…")

    # 2) 本配信（同じ日に二重に送らない）
    if kv_get("meta:last_sent") == campaign and not args.force:
        log(f"[skip] {campaign} は配信済み")
        return 0
    active = [k for k in subs if (k.get("metadata") or {}).get("s") == "active"]
    log(f"[newsletter] {digest['subject']} → {len(active)} 人（{provider()}）")
    sent = failed = 0
    for key in active:
        if budget <= 0:
            log(f"[warn] 1日の上限 {DAILY_MAX} 通に達したため、残り {len(active) - sent - failed} 人には送りません")
            break
        meta = key["metadata"]
        sub_id = key["name"].split(":", 1)[1]
        page_url, one_click = unsubscribe_links(sub_id, meta["t"])
        body, text = render(digest, page_url)
        headers = {"List-Unsubscribe": f"<{one_click}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if send_mail(meta["e"], digest["subject"], body, text, headers):
            sent += 1
            budget -= 1
        else:
            failed += 1
        time.sleep(0.2)
    if sent or not active:
        kv_put("meta:last_sent", campaign)
    log(f"[done] 送信 {sent} / 失敗 {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
