"""Bluesky の投稿選び（本数・間隔・埋め合わせ・重複よけ）を外部通信なしで検証する。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import bluesky_post as bp

NOW = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)  # 13:00 JST


def iso(dt):
    return dt.isoformat(timespec='seconds')


def article(slug, title, minutes_ago=60, **extra):
    body = '## 日本で使える？\n' + 'あ' * 2000
    base = {'slug': slug, 'title': title, 'body': body, 'genreSlug': 'ai', 'tags': ['AI'],
            'generatedAt': iso(NOW - timedelta(minutes=minutes_ago)), 'description': 'd'}
    base.update(extra)
    return base


def mirror(minutes_ago, **extra):
    entry = {'style': 'standalone', 'dueAt': iso(NOW - timedelta(minutes=minutes_ago))}
    entry.update(extra)
    return entry


class ChooseTest(unittest.TestCase):
    def setUp(self):
        # 記事の投稿は動画つきだけ（ルール8）。選び方の試験では、どの記事にも動画がある前提にする
        p = patch.object(bp, 'video_of', lambda slug, videos=None: {'url': f'https://ai-tech-express.jp/video/{slug}.mp4', 'variant': 'short'})
        p.start()
        self.addCleanup(p.stop)

    def test_only_one_normal_post_per_run(self):
        state = {'posted': {'a': mirror(30), 'b': mirror(25)}}
        picked = bp.choose(state, {}, NOW, fill=True, articles=[])
        self.assertEqual([(k, kind) for k, _, kind in picked], [('a', 'mirror')])

    def test_gap_blocks_normal_and_fill(self):
        state = {'posted': {'a': mirror(30), 'old': mirror(60, blueskyAt=iso(NOW - timedelta(minutes=10)))}}
        self.assertEqual(bp.choose(state, {}, NOW, fill=True, articles=[article('x', 'X')]), [])
        extra = {'f': {'at': iso(NOW - timedelta(minutes=bp.GAP_MIN - 1)), 'title': 'F'}}
        self.assertEqual(bp.choose({'posted': {'a': mirror(30)}}, extra, NOW, fill=True, articles=[]), [])

    def test_breaking_ignores_gap_and_blocks_normal_in_same_run(self):
        state = {'posted': {
            'n': mirror(30),
            'brk': {'breaking': True, 'dueAt': iso(NOW - timedelta(minutes=2))},
            'old': mirror(60, blueskyAt=iso(NOW - timedelta(minutes=5))),
        }}
        picked = bp.choose(state, {}, NOW, fill=True, articles=[])
        self.assertEqual([k for k, _, _ in picked], ['brk'])

    def test_daily_cap_counts_fill_posts(self):
        today = NOW.replace(hour=0)  # 9:00 JST
        extra = {f'f{i}': {'at': iso(today), 'title': f't{i}'} for i in range(bp.PER_DAY)}
        state = {'posted': {'brk': {'breaking': True, 'dueAt': iso(NOW)}}}
        self.assertEqual(bp.posted_today(state, NOW, extra), bp.PER_DAY)
        self.assertEqual(bp.choose(state, extra, NOW, fill=True, articles=[]), [])

    def test_fill_when_no_mirror_pending(self):
        arts = [article('old', '古い記事', minutes_ago=60 * 13), article('new', '新しい記事', minutes_ago=30),
                article('mid', '少し前の記事', minutes_ago=120)]
        picked = bp.choose({'posted': {}}, {}, NOW, fill=True, articles=arts)
        self.assertEqual([(k, kind) for k, _, kind in picked], [('new', 'fill')])
        self.assertEqual(bp.choose({'posted': {}}, {}, NOW, fill=False, articles=arts), [])

    def test_fill_skips_too_fresh_taken_thin_and_breaking_reserved(self):
        arts = [
            article('fresh', '公開直後', minutes_ago=5),
            article('threads', 'Threads予定', minutes_ago=20),
            article('done', '埋め合わせ済み', minutes_ago=25),
            article('thin', '短い', minutes_ago=30, body='短い'),
            article('brk', '速報待ち', minutes_ago=35, breaking=True),
            article('ok', '使える記事', minutes_ago=40),
        ]
        state = {'posted': {'threads': mirror(-60)}}
        extra = {'done': {'failed': 'x'}}
        self.assertEqual(bp.fill_candidate(state, extra, NOW, arts)['slug'], 'ok')

    def test_fill_skips_same_topic_posted_recently(self):
        arts = [article('genie-b', 'Google DeepMindが新AI『Genie 4』発表 映像と音声が動的に連動', minutes_ago=30),
                article('other', 'Mistralが新モデルを公開', minutes_ago=90)]
        extra = {'genie-a': {'at': iso(NOW - timedelta(hours=3)),
                             'title': 'Google DeepMind、言葉で遊べる世界を作る新AI「Genie 4」を発表'}}
        self.assertEqual(bp.fill_candidate({'posted': {}}, extra, NOW, arts)['slug'], 'other')

    def test_similar_titles(self):
        self.assertTrue(bp.similar('OpenAIが「GPT-6」を発表', 'GPT-6の詳細、OpenAI『GPT-6』公開'))
        self.assertFalse(bp.similar('AI半導体のNVIDIAが英国AI構想を発表 創薬からロボットまで変える次世代基盤',
                                    'AI半導体のNVIDIAが医療向けAI発表 創薬から手術支援まで自動化を促進'))

    def test_pending_skips_articles_already_filled(self):
        state = {'posted': {'a': mirror(30)}}
        self.assertEqual(bp.pending(state, NOW, {'a': {'at': iso(NOW)}}), [])
        self.assertEqual([k for k, _ in bp.pending(state, NOW)], ['a'])

    def test_fill_plan_uses_fill_campaign(self):
        art = article('x', 'Xの新機能', threadsStyle=2, threadsPost='Xが新機能を公開しました。\n日本でも使えます。👇')
        p = bp.plan_fill(art)
        self.assertIn('utm_campaign=fill', p['link'])
        self.assertIn('utm_source=bluesky', p['link'])
        self.assertFalse(p['text'].endswith('👇'))


class MergeExtraTest(unittest.TestCase):
    def test_union_keeps_existing_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'extra.json'
            mine = Path(directory) / 'mine.json'
            target.write_text(json.dumps({'a': {'uri': 'remote', 'at': iso(NOW)}}))
            mine.write_text(json.dumps({'a': {'uri': 'mine', 'at': iso(NOW)}, 'b': {'uri': 'b', 'at': iso(NOW)}}))
            with patch.object(bp, 'EXTRA_PATH', target):
                bp.merge_extra(str(mine))
            merged = json.loads(target.read_text())
        self.assertEqual(merged['a']['uri'], 'remote')
        self.assertEqual(merged['b']['uri'], 'b')

    def test_missing_mine_file_is_harmless(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'extra.json'
            target.write_text(json.dumps({'a': {'uri': 'remote'}}))
            with patch.object(bp, 'EXTRA_PATH', target):
                bp.merge_extra(str(Path(directory) / 'none.json'))
            self.assertEqual(json.loads(target.read_text()), {'a': {'uri': 'remote'}})


if __name__ == '__main__':
    unittest.main()
