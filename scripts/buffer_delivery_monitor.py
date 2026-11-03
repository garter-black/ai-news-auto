#!/usr/bin/env python3
"""Read-only Buffer delivery monitor. Never modifies a post, queue, or state file.

Only counts and sanitized classifications leave this process; no post text, asset URLs,
IDs or raw provider errors are written to CI logs or GitHub summaries.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buffer_budget
from threads_post import buffer_account, buffer_graphql

SERVICES = ('threads', 'twitter', 'facebook')
LABELS = {'threads': 'Threads', 'twitter': 'X', 'facebook': 'Facebook'}
FIELDS = '''id status channelId createdAt dueAt sentAt externalLink
error { message rawError }
assets { type source }
metadata { __typename
 ... on ThreadsPostMetadata { thread { assets { type source } } }
 ... on TwitterPostMetadata { thread { assets { type source } } }
}'''


def utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def query_posts(key: str, organization: str, channel_ids: list[str], status: list[str], since: datetime | None = None) -> list[dict]:
    """Fail closed on incomplete pagination; do not combine sent with other statuses."""
    cursor = None
    seen = set()
    results = []
    filt = f'status: [{", ".join(status)}], channelIds: {json.dumps(channel_ids)}'
    if since:
        filt += ', startDate: ' + json.dumps(since.isoformat(timespec='seconds').replace('+00:00', 'Z'))
    for _ in range(100):
        after = ', after: ' + json.dumps(cursor) if cursor else ''
        data = buffer_graphql(key, f'''query {{ posts(first: 50{after}, input: {{ organizationId: {json.dumps(organization)}, filter: {{ {filt} }} }}) {{
            edges {{ node {{ {FIELDS} }} }} pageInfo {{ endCursor hasNextPage }} }} }}''')
        page = data['posts']
        results.extend(edge['node'] for edge in page['edges'])
        if not page['pageInfo']['hasNextPage']:
            return results
        nxt = page['pageInfo']['endCursor']
        if not nxt or nxt in seen:
            raise ValueError('incomplete pagination')
        seen.add(nxt)
        cursor = nxt
    raise ValueError('pagination limit')


def error_kind(node: dict) -> str:
    error = node.get('error') or {}
    value = (str(error.get('message') or '') + ' ' + str(error.get('rawError') or '')).lower()
    if any(x in value for x in ('media', 'image', '画像')) and any(x in value for x in ('fetch', 'access', '404', 'uri', 'url', '取得')):
        return '画像取得関連'
    return 'その他・要確認'


def image_urls(node: dict) -> list[str]:
    assets = list(node.get('assets') or [])
    for item in ((node.get('metadata') or {}).get('thread') or []):
        assets.extend(item.get('assets') or [])
    return list(dict.fromkeys(a.get('source') for a in assets if str(a.get('type', '')).lower() == 'image' and a.get('source')))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_image(url: str) -> str:
    """Our origin or Buffer image proxy only, no redirects; reject 200 HTML challenges."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('ai-tech-express.jp', 'images.buffer.com') or parsed.username or parsed.password or parsed.port not in (None, 443):
            return 'external_unchecked'
        req = urllib.request.Request(url, headers={'User-Agent': 'SaisokuPublishCheck/1.0 (+https://ai-tech-express.jp)', 'Accept': 'image/*'})
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as resp:
            head = resp.read(16)
            mime = resp.headers.get('Content-Type', '').lower().split(';', 1)[0]
            valid = head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or (head.startswith(b'RIFF') and head[8:12] == b'WEBP')
            return 'ok' if resp.status == 200 and mime.startswith('image/') and valid else 'unavailable'
    except urllib.error.HTTPError as exc:
        exc.close()
        return 'unavailable'
    except (OSError, ValueError, urllib.error.URLError):
        return 'unavailable'


# 送信済みは直近7日だけ数える。取得もその少し前から（全期間を毎回ページ送りしていたのをやめ、呼び出し回数を抑える）
SENT_LOOKBACK = timedelta(days=8)


def collect(key: str, now: datetime, image_checker=check_image, known_sent_id: str | None = None) -> dict:
    if not key:
        raise ValueError('credential unavailable')
    # 組織とチャンネルの ID は保存したもの（state/buffer_channels.json）を使う（毎回2回の問い合わせを省く）
    orgs = buffer_account(key, gql=buffer_graphql)
    if not orgs:
        raise ValueError('no organization')
    channels = {}
    posts = {}
    for org in orgs:
        oid = org['id']
        items = org['channels']
        selected = [c for c in items if str(c.get('service', '')).lower() in SERVICES]
        channels.update({c['id']: str(c['service']).lower() for c in selected})
        ids = [c['id'] for c in selected]
        if not ids:
            continue
        # Sent is deliberately queried separately: mixed sent/non-sent filters have returned empty results.
        for states, since in [(['sent'], now - SENT_LOOKBACK), (['scheduled', 'sending', 'needs_approval', 'draft', 'error'], None)]:
            for node in query_posts(key, oid, ids, states, since):
                if node.get('id') in posts:
                    raise ValueError('duplicate post id in listing')
                posts[node['id']] = node
    if not posts:
        raise ValueError('empty provider listing cannot be verified')
    if known_sent_id and (known_sent_id not in posts or posts[known_sent_id].get('status') != 'sent'):
        raise ValueError('known sent post absent from provider listing')
    if not set(SERVICES).issubset(set(channels.values())):
        raise ValueError('expected channel missing')
    counts = {service: Counter() for service in SERVICES}
    errors = Counter()
    recent_errors = 0
    overdue = 0
    checked_urls = {}
    for node in posts.values():
        service = channels.get(node.get('channelId'))
        if not service:
            raise ValueError('unknown channel')
        status = node.get('status')
        if status == 'draft':
            continue  # drafts are not accepted for delivery
        if status not in ('sent', 'scheduled', 'sending', 'needs_approval', 'error'):
            raise ValueError('unknown status')
        if status == 'sent':
            sent_at = utc(node.get('sentAt'))
            if not sent_at:
                raise ValueError('sent post missing sentAt')
            if sent_at < now - timedelta(days=7):
                continue
        counts[service][status] += 1
        if status == 'error':
            errors[error_kind(node)] += 1
            # No usable timestamp means unknown age, never silently mark it historical.
            when = utc(node.get('dueAt')) or utc(node.get('createdAt'))
            if not when or when >= now - timedelta(hours=2):
                recent_errors += 1
        if status in ('scheduled', 'sending'):
            due = utc(node.get('dueAt'))
            if due and due < now - timedelta(minutes=30):
                overdue += 1
            if due and now - timedelta(minutes=30) <= due <= now + timedelta(hours=2):
                for url in image_urls(node):
                    if url not in checked_urls:
                        checked_urls[url] = image_checker(url)
    image_counts = Counter(checked_urls.values())
    return {'counts': counts, 'errors': errors, 'recent_errors': recent_errors, 'overdue': overdue, 'images': image_counts}


def render(report: dict, now: datetime) -> str:
    lines = ['## Buffer 投稿状態（読み取り専用）', '',
             f'確認時刻: {now.astimezone(timezone(timedelta(hours=9))):%Y-%m-%d %H:%M} JST', '',
             '送信済みは直近7日、未送信・失敗は期間を限定せず取得。Buffer上の状態であり、SNS画面での表示を保証しません。', '',
             '| 媒体 | 送信済み | 予約中 | 送信処理中 | 承認待ち | エラー |', '|---|---:|---:|---:|---:|---:|']
    for service in SERVICES:
        c = report['counts'][service]
        lines.append(f"| {LABELS[service]} | {c['sent']} | {c['scheduled']} | {c['sending']} | {c['needs_approval']} | {c['error']} |")
    lines += ['', f"失敗の内訳: 画像取得関連 {report['errors']['画像取得関連']}件、その他・要確認 {report['errors']['その他・要確認']}件。",
              f"予定時刻を30分超過して未送信: {report['overdue']}件。",
              f"2時間以内に予約された画像の事前確認（自社・Buffer経由）: 正常 {report['images']['ok']}件、取得不可 {report['images']['unavailable']}件、外部URLのため未確認 {report['images']['external_unchecked']}件。",
              '画像確認は自社・Bufferの画像URLに限り、Buffer・SNS側からの取得成功を保証しません。',
              '', '[Bufferで対象の投稿を確認](https://publish.buffer.com/)。エラーや受理不明は再送前にSNS実画面で重複を確認してください。',
              'この監視は投稿・再送・削除・予約変更・履歴変更を行いません。']
    return '\n'.join(lines) + '\n'


def known_sent_anchor(now: datetime) -> str | None:
    """Cross-check against one recent previously observed sent post when available."""
    path = Path(__file__).resolve().parent.parent / 'state' / 'threads_posted.json'
    try:
        entries = json.loads(path.read_text(encoding='utf-8')).get('posted', {}).values()
        candidates = []
        for entry in entries:
            when = utc(entry.get('sentAt'))
            if entry.get('via') == 'buffer' and entry.get('id') and when and now - timedelta(days=7) <= when <= now:
                candidates.append((when, entry['id']))
        return max(candidates)[1] if candidates else None
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('could not read existing delivery anchor')


def main() -> int:
    now = datetime.now(timezone.utc)
    buffer_budget.set_role('monitor')  # 読むだけ。予約の分の回数を残す
    try:
        buffer_budget.check('monitor', now)
    except buffer_budget.BufferUnavailable:
        # 上限・429 の待ち時間中は確認を見送る（Buffer を呼ばない）。予約を優先するため
        summary = '## Buffer 投稿状態\n\nBuffer の呼び出し上限・待ち時間のため、今回の確認は見送りました（予約を優先）。\n'
        print(summary)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary, encoding='utf-8')
        return 0
    try:
        report = collect(os.environ.get('BUFFER_API_KEY', '').strip(), now, known_sent_id=known_sent_anchor(now))
        summary = render(report, now)
    except Exception:
        # API exceptions may contain sensitive provider response; never echo them.
        summary = '## Buffer 投稿状態\n\n確認に失敗しました。成功・失敗は不明です。Bufferの接続と監視設定を確認してください。\n'
        print(summary)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary, encoding='utf-8')
        return 2
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(summary, encoding='utf-8')
    if report['recent_errors'] or report['overdue'] or report['images']['unavailable']:
        print('::error::新しい投稿エラー、予定超過、または画像取得不可を検出しました。Bufferを確認してください。')
        return 1
    if sum(report['errors'].values()):
        print('::warning::過去の投稿エラーがBufferに残っています。自動再送はしません。')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
