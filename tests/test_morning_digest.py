"""日次まとめの重複・部分成功・遅延を外部通信なしで検証する。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import morning_digest as md
import bluesky_post as bp

NOW = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)  # Friday 15:00 JST
CHANNELS = {'threads': {'id': 'thread-channel'}, 'twitter': {'id': 'x-channel'}}


def post(kind='digest', now=NOW):
    return {'key': f'{kind}-{now.astimezone(md.tp.JST):%Y%m%d}',
            'text': md.compose([{'title': 'Googleの新機能'}, {'title': 'Anthropicの更新'}], now) if kind == 'digest' else 'AIの問いかけ（2026/10/30）\nどれ？\n①A\n②B',
            'reply': md.REPLY_LINES[0], 'link': 'https://ai-tech-express.jp/news',
            'x_text': '今日のAIニュース2本（2026/10/30）\n・Google\n・Anthropic',
            'slot': md.tp.DIGEST_SLOT_JST if kind == 'digest' else md.tp.POLL_SLOT_JST,
            'items': ['google', 'anthropic']}


def remote(pid, text, status='sent'):
    return {'id': pid, 'text': text, 'status': status, 'createdAt': NOW.isoformat(),
            'sentAt': NOW.isoformat() if status == 'sent' else None,
            'externalLink': 'https://example.test/post/' + pid}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


class WindowAndTextTests(unittest.TestCase):
    def at(self, hour, minute=0, day=30):
        return datetime(2026, 10, day, hour, minute, tzinfo=md.tp.JST)

    def test_catchup_windows_and_weekdays(self):
        for hour, minute, expected in [(6,29,False),(6,30,True),(10,39,True),(17,59,True),(18,0,False)]:
            self.assertEqual(md.in_window('digest', self.at(hour, minute)), expected)
        # 問いかけ投稿は 2026-11-03 に停止。どの曜日・時刻でも出さない
        for hour in (10, 11, 15, 19, 20):
            self.assertFalse(md.in_window('poll', self.at(hour)))

    def test_late_digest_has_no_morning_claim_and_stays_same_daily_match(self):
        items = [{'title': 'Googleの新機能'}, {'title': 'Anthropicの更新'}]
        morning = md.compose(items, self.at(7))
        late = md.compose(items, self.at(15))
        self.assertIn('今朝', morning)
        self.assertIn('今日', late)
        self.assertNotIn('今朝', late)
        self.assertTrue(md.matches_daily(remote('old', morning), 'digest', NOW))
        self.assertFalse(md.matches_daily(remote('old', morning.replace('2026/10/30','2026/10/29')), 'digest', NOW))

    def test_poll_x_keeps_four_options_and_date(self):
        body = 'ふだんAIをいちばん使う場面は？\n\n①調べもの\n②文章づくり\n③プログラミング\n④画像・動画づくり\n\n番号だけでも大歓迎です。理由もあればぜひ。'
        text = md.compose_poll_x(body, NOW)
        for marker in ['①','②','③','④','2026/10/30']:
            self.assertIn(marker, text)
        self.assertLessEqual(md.tp.x_weight_url(text), 280)

    def test_poll_long_news_choices_are_all_retained(self):
        choices = '\n'.join(mark + '長いニュースの見出しです' * 4 for mark in '①②③')
        text = md.compose_poll_x('今週のAIニュースで、いちばん気になったのは？\n\n' + choices + '\n\nコメントで教えてください。', NOW)
        self.assertLessEqual(md.tp.x_weight_url(text), 280)
        self.assertTrue(all(mark in text for mark in '①②③'))

    def test_legacy_detection_is_current_day_and_kind_only(self):
        node = remote('legacy','今朝のAIニュース3本\n・更新')
        self.assertTrue(md.matches_daily(node, 'digest', NOW))
        self.assertFalse(md.matches_daily(node, 'poll', NOW))
        node['createdAt'] = node['sentAt'] = (NOW-timedelta(days=1)).isoformat()
        self.assertFalse(md.matches_daily(node, 'digest', NOW))


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.state = {'posted': {'normal-article': {'id': 'normal', 'format': 'classic'}}}
        self.saved = []
        self.remote = {c['id']: [] for c in CHANNELS.values()}
        self.created = []
        self.fail = {}
        self.save_patch = patch.object(md.tp, 'save_state', side_effect=lambda s: self.saved.append(copy.deepcopy(s)))
        self.list_patch = patch.object(md, 'buffer_posts', side_effect=lambda k, channel, now: copy.deepcopy(self.remote[channel]))
        self.create_patch = patch.object(md.tp, 'create_post', side_effect=self.create)
        self.save_patch.start(); self.list_patch.start(); self.create_mock = self.create_patch.start()
        self.addCleanup(self.save_patch.stop); self.addCleanup(self.list_patch.stop); self.addCleanup(self.create_patch.stop)

    def create(self, key, payload, **gate):
        channel = payload['channelId']
        self.created.append(copy.deepcopy(payload))
        if channel in self.fail:
            raise self.fail[channel]
        pid = f'post-{channel}'
        self.remote[channel].append(remote(pid, payload['text']))
        return pid

    def run_post(self, data=None):
        return md.deliver('fake', CHANNELS, self.state, data or post(), 'digest', NOW)

    def test_success_is_saved_before_second_create_and_replay_is_idempotent(self):
        self.assertEqual(self.run_post(), 0)
        entry = self.state['posted']['digest-20261030']
        self.assertEqual(entry['id'], 'post-thread-channel')
        self.assertEqual(entry['channels']['twitter'], 'post-x-channel')
        partial = [s['posted'].get('digest-20261030') for s in self.saved]
        self.assertTrue(any(e and e.get('id') and not e.get('channels',{}).get('twitter') for e in partial))
        self.assertEqual(self.run_post(), 0)
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.state['posted']['normal-article'], {'id': 'normal', 'format': 'classic'})

    def test_x_failure_returns_nonzero_and_only_x_is_retried(self):
        self.fail['x-channel'] = RuntimeError('Buffer: rejected')
        self.assertEqual(self.run_post(), 1)
        self.assertEqual(self.state['posted']['digest-20261030']['delivery']['twitter']['status'], 'rejected')
        self.fail.clear()
        self.assertEqual(self.run_post(), 0)
        self.assertEqual([p['channelId'] for p in self.created], ['thread-channel','x-channel','x-channel'])

    def test_threads_failure_preserves_x_in_pending_and_retries_only_threads(self):
        self.fail['thread-channel'] = RuntimeError('Buffer: rejected')
        self.assertEqual(self.run_post(), 1)
        self.assertNotIn('digest-20261030', self.state['posted'])
        self.assertEqual(self.state['extraPending']['digest-20261030']['channels']['twitter'], 'post-x-channel')
        self.fail.clear()
        self.assertEqual(self.run_post(), 0)
        self.assertEqual([p['channelId'] for p in self.created], ['thread-channel','x-channel','thread-channel'])
        self.assertNotIn('digest-20261030', self.state['extraPending'])

    def test_late_threads_retry_updates_bluesky_due_without_recreating_x(self):
        morning=datetime(2026,10,30,6,33,tzinfo=md.tp.JST)
        self.fail['thread-channel']=RuntimeError('Buffer: rejected')
        self.assertEqual(md.deliver('fake',CHANNELS,self.state,post(),'digest',morning),1)
        pending=self.state['extraPending']['digest-20261030']
        self.assertEqual(datetime.fromisoformat(pending['dueAt']).astimezone(md.tp.JST).hour,7)
        x_id=pending['channels']['twitter']
        self.fail.clear()
        self.assertEqual(self.run_post(),0)
        entry=self.state['posted']['digest-20261030']
        self.assertEqual(entry['dueAt'],NOW.isoformat(timespec='seconds'))
        self.assertEqual(entry['postedAt'],NOW.isoformat(timespec='seconds'))
        self.assertEqual(entry['channels']['twitter'],x_id)
        self.assertEqual([key for key,_ in bp.pending(self.state,NOW)],['digest-20261030'])
        self.assertEqual([p['channelId'] for p in self.created],['thread-channel','x-channel','thread-channel'])

    def test_timeout_never_blindly_recreates(self):
        self.fail['thread-channel'] = TimeoutError()
        self.assertEqual(self.run_post(), 1)
        self.fail.clear()
        self.assertEqual(self.run_post(), 1)
        self.assertEqual(len(self.created), 2)
        self.assertEqual(self.state['extraPending']['digest-20261030']['delivery']['threads']['status'], 'unknown')

    def test_unknown_outcome_recovers_when_remote_post_appears(self):
        self.fail['thread-channel'] = TimeoutError()
        self.run_post(); self.fail.clear()
        self.remote['thread-channel'].append(remote('actually-accepted', post()['text']))
        self.assertEqual(self.run_post(), 0)
        self.assertEqual(self.state['posted']['digest-20261030']['id'], 'actually-accepted')
        self.assertEqual(len(self.created), 2)

    def test_timeout_recovery_uses_first_remote_sent_time_for_bluesky_only_once(self):
        morning=datetime(2026,10,30,6,33,tzinfo=md.tp.JST)
        self.fail['thread-channel']=TimeoutError()
        self.assertEqual(md.deliver('fake',CHANNELS,self.state,post(),'digest',morning),1)
        self.fail.clear()
        accepted=remote('recovered-t',post()['text'])
        accepted['createdAt']=morning.isoformat()
        accepted['dueAt']=morning.replace(hour=7,minute=30).isoformat()
        self.remote['thread-channel']=[accepted]
        self.assertEqual(self.run_post(),0)
        entry=self.state['posted']['digest-20261030']
        self.assertEqual(entry['dueAt'],NOW.isoformat())  # sentAt takes priority over the old scheduled time.
        self.assertEqual(entry['postedAt'],morning.isoformat())
        self.assertEqual([key for key,_ in bp.pending(self.state,NOW)],['digest-20261030'])
        self.assertEqual(len(self.created),2)
        md.record_remote(entry,'threads',{**accepted,'sentAt':(NOW+timedelta(hours=1)).isoformat()},NOW+timedelta(hours=1))
        self.assertEqual(entry['dueAt'],NOW.isoformat())
        self.assertEqual(entry['postedAt'],morning.isoformat())

    def test_missing_channel_cannot_turn_unknown_outcome_into_retry_permission(self):
        self.fail['thread-channel'] = TimeoutError()
        self.assertEqual(self.run_post(),1)
        self.fail.clear()
        self.assertEqual(md.deliver('fake',{'twitter':CHANNELS['twitter']},self.state,post(),'digest',NOW),1)
        self.assertEqual(self.state['extraPending']['digest-20261030']['delivery']['threads']['status'],'unknown')
        self.assertEqual(self.run_post(),1)
        self.assertEqual([p['channelId'] for p in self.created],['thread-channel','x-channel'])

    def test_git_history_loss_adopts_existing_posts_even_if_content_changed(self):
        self.remote['thread-channel'].append(remote('existing-t', post()['text'].replace('Googleの新機能','別のニュース')))
        self.remote['x-channel'].append(remote('existing-x', post()['x_text'].replace('Google','別のニュース')))
        self.assertEqual(self.run_post(), 0)
        self.assertEqual(self.created, [])
        self.assertEqual(self.state['posted']['digest-20261030']['id'], 'existing-t')

    def test_existing_error_is_retained_but_never_recreated_or_sent_to_bluesky(self):
        self.remote['thread-channel'].append(remote('failed-t', post()['text'], 'error'))
        self.assertEqual(self.run_post(), 1)
        self.assertNotIn('digest-20261030', self.state['posted'])
        self.assertEqual(self.state['extraPending']['digest-20261030']['id'], 'failed-t')
        self.assertEqual([p['channelId'] for p in self.created], ['x-channel'])

    def test_duplicate_remote_matches_fail_closed_for_that_medium(self):
        self.remote['thread-channel'] = [remote('one',post()['text']),remote('two',post()['text'])]
        self.assertEqual(self.run_post(), 1)
        self.assertFalse(any(p['channelId']=='thread-channel' for p in self.created))

    def test_listing_failure_happens_before_any_mutation(self):
        with patch.object(md,'buffer_posts',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError): self.run_post()
        self.assertEqual(self.created, [])

    def test_missing_target_channel_is_failure_not_silent_success(self):
        self.assertEqual(md.deliver('fake', {'threads': CHANNELS['threads']}, self.state, post(),'digest',NOW),1)
        self.assertEqual(self.state['posted']['digest-20261030']['delivery']['twitter']['status'],'rejected')

    def test_failed_save_after_acceptance_stops_before_other_medium(self):
        def save(state):
            if md.daily_entry(state,'digest-20261030').get('id'):
                raise OSError('disk unavailable')
        with patch.object(md.tp,'save_state',side_effect=save):
            with self.assertRaises(OSError): self.run_post()
        self.assertEqual([p['channelId'] for p in self.created], ['thread-channel'])

    def test_old_plan_and_missing_x_body_are_rejected_before_creating(self):
        old=post();old['key']='digest-20261029'
        with self.assertRaises(RuntimeError):self.run_post(old)
        empty=post();empty['x_text']=''
        with self.assertRaises(RuntimeError):self.run_post(empty)
        self.assertEqual(self.created,[])

    def test_existing_id_is_never_overwritten_with_a_different_remote_match(self):
        self.state['posted']['digest-20261030']={'id':'original','style':'digest','channels':{}}
        self.remote['thread-channel']=[remote('different',post()['text'])]
        self.assertEqual(self.run_post(),1)
        self.assertEqual(md.daily_entry(self.state,'digest-20261030')['id'],'original')
        self.assertFalse(any(p['channelId']=='thread-channel' for p in self.created))

    def test_verify_is_read_only_and_distinguishes_error(self):
        self.remote['thread-channel'] = [remote('t',post()['text'])]
        self.remote['x-channel'] = [remote('x',post()['x_text'],'error')]
        before=copy.deepcopy(self.state)
        self.assertEqual(md.verify('fake',CHANNELS,self.state,'digest-20261030','digest',NOW),1)
        self.assertEqual(self.saved,[]); self.assertEqual(self.created,[]); self.assertEqual(before,self.state)


class MergeAndQueryTests(unittest.TestCase):
    def test_draft_delete_rejection_is_not_reported_as_success(self):
        with patch.object(md.tp,'buffer_graphql',return_value={'deletePost':{'__typename':'MutationError','message':'denied'}}):
            with self.assertRaises(RuntimeError):md.delete_post('fake','draft')

    def test_draft_delete_success_is_accepted(self):
        with patch.object(md.tp,'buffer_graphql',return_value={'deletePost':{'__typename':'DeletePostSuccess'}}):
            self.assertIsNone(md.delete_post('fake','draft'))

    def test_merge_fills_partial_without_clobbering_other_channels_or_metrics(self):
        old={'id':'t','style':'digest','channels':{'bluesky':'b'},'metrics':{'views':4}}
        new={'id':'t','style':'digest','channels':{'twitter':'x'},'postPlan':post()}
        actual=md.merge_entry(old,new)
        self.assertEqual(actual['channels'],{'twitter':'x','bluesky':'b'})
        self.assertEqual(actual['metrics'],{'views':4})
        with self.assertRaises(RuntimeError):md.merge_entry(old,{'id':'other','style':'digest'})

    def test_merge_promotes_partial_pending_after_threads_success(self):
        state={'posted':{},'extraPending':{'digest-20261030':{'style':'digest','channels':{'twitter':'x'}}}}
        mine={'posted':{'digest-20261030':{'id':'t','style':'digest','channels':{},'delivery':{'threads':{'status':'accepted','updatedAt':NOW.isoformat()}}}}}
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'mine.json';file.write_text(json.dumps(mine))
            with patch.object(md.tp,'load_state',return_value=state), patch.object(md.tp,'save_state'):
                self.assertEqual(md.merge(str(file)),0)
        self.assertEqual(state['posted']['digest-20261030']['channels']['twitter'],'x')
        self.assertNotIn('digest-20261030',state['extraPending'])

    def test_stale_pending_does_not_downgrade_saved_acceptance(self):
        saved={'id':'t','style':'digest','delivery':{'threads':{'status':'accepted','updatedAt':'2026-10-30T06:00:00Z'}}}
        pending={'style':'digest','delivery':{'threads':{'status':'unknown','updatedAt':'2026-10-30T06:00:01Z'}}}
        self.assertEqual(md.merge_entry(saved,pending)['delivery']['threads']['status'],'accepted')

    def test_buffer_pagination_and_all_statuses(self):
        responses=[{'account':{'organizations':[{'id':'org'}]}},
                   {'posts':{'edges':[{'node':remote('t',post()['text'],'scheduled')}],'pageInfo':{'hasNextPage':True,'endCursor':'next'}}},
                   {'posts':{'edges':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}}]
        with patch.object(md.tp,'buffer_graphql',side_effect=responses) as api:
            rows=md.buffer_posts('fake','channel',NOW)
        self.assertEqual(rows[0]['status'],'scheduled')
        self.assertIn('after: "next"',api.call_args_list[-1].args[1])
        self.assertNotIn('status:',api.call_args_list[1].args[1])
        self.assertIn('startDate:',api.call_args_list[1].args[1])

    def test_incomplete_remote_pagination_fails_closed(self):
        responses=[{'account':{'organizations':[{'id':'org'}]}},{'posts':{'edges':[],'pageInfo':{'hasNextPage':True,'endCursor':None}}}]
        with patch.object(md.tp,'buffer_graphql',side_effect=responses):
            with self.assertRaises(RuntimeError):md.buffer_posts('fake','channel',NOW)


class BlueskyCompatibilityTests(unittest.TestCase):
    def test_old_digest_uses_recorded_date_and_neutral_card_title(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'old.json').write_text(json.dumps({'slug':'old','title':'前日のニュース'}))
            with patch.object(bp.tp,'ARTICLES_DIR',Path(directory)):
                result=bp.plan('digest-20261029',{'style':'digest','items':['old'],'dueAt':'2026-10-28T22:30:00+00:00'})
        self.assertIn('2026/10/29',result['text'])
        self.assertIn('今朝',result['text'])
        self.assertEqual(result['title'],'AIニュースまとめ｜サイソクニュース')

    def test_new_late_digest_uses_exact_saved_text_even_without_article_files(self):
        text='今日のAIニュース3本（2026/10/30）\n・確認済みの本文'
        result=bp.plan('digest-20261030',{'style':'digest','items':[],'postPlan':{'text':text}})
        self.assertEqual(result['text'],text)
        self.assertNotIn('けさ',result['title'])

    def test_poll_is_still_not_a_bluesky_candidate(self):
        self.assertIsNone(bp.plan('poll-20261030',{'style':'poll','postPlan':{'text':'問いかけ'}}))

    def test_merge_keeps_accepted_bluesky_uri_when_digest_is_temporarily_pending(self):
        key='digest-20261030'
        state={'posted':{},'extraPending':{key:{'style':'digest','id':'t','channels':{}}}}
        mine={'posted':{key:{'style':'digest','channels':{'bluesky':'at://accepted'},'blueskyAt':NOW.isoformat()}}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mine.json';path.write_text(json.dumps(mine))
            with patch.object(bp.tp,'load_state',return_value=state),patch.object(bp.tp,'save_state'):
                self.assertEqual(bp.merge(str(path)),0)
        self.assertEqual(state['extraPending'][key]['channels']['bluesky'],'at://accepted')
        self.assertNotIn(key,state['posted'])
        md.record_remote(state['extraPending'][key],'threads',remote('t','text'),NOW)
        md.store_entry(state,key,state['extraPending'][key],save=False)
        self.assertEqual(bp.pending(state,NOW),[])


class CommandGuardsTests(unittest.TestCase):
    def main(self,args,env=None):
        with patch.object(sys,'argv',['morning_digest.py',*args]),patch.object(md,'datetime',Clock),patch.dict(os.environ,env or {},clear=True),patch.object(md.tp,'load_state',return_value={'posted':{}}),patch.object(md.tp,'DIGEST_ON',True):
            return md.main()

    def test_yesterday_is_never_sent(self):
        with patch.object(md.tp,'create_post') as create:
            self.assertEqual(self.main(['--date','2026-10-29'],{'THREADS_MODE':'live'}),1)
            create.assert_not_called()

    def test_live_force_stays_forbidden(self):
        with patch.object(md.tp,'create_post') as create:
            self.assertEqual(self.main(['--force'],{'THREADS_MODE':'live'}),1)
            create.assert_not_called()

    def test_dry_run_never_looks_up_credentials_or_writes_history(self):
        with patch.object(md,'build',return_value=post()),patch.object(md.tp,'buffer_channels') as channels,patch.object(md.tp,'save_state') as save,patch.object(md.tp,'create_post') as create:
            self.assertEqual(self.main(['--dry-run'],{'THREADS_MODE':'live'}),0)
            channels.assert_not_called();save.assert_not_called();create.assert_not_called()

    def test_missing_key_is_a_failure(self):
        with patch.object(md,'build',return_value=post()):
            self.assertEqual(self.main([],{'THREADS_MODE':'live'}),1)

    def test_verify_cannot_be_combined_with_merge(self):
        with patch.object(md,'merge') as merge:
            self.assertEqual(self.main(['--verify','--merge','not-read.json']),1)
            merge.assert_not_called()


def digest_post(now=NOW):
    items = [{'slug': 'nvidia', 'title': 'AI半導体のNVIDIAが医療向けAI発表 創薬から手術支援まで'},
             {'slug': 'genie', 'title': 'Google DeepMind、言葉で遊べる世界を作る新AI「Genie 4」を発表 操作も自由自在', 'channel': 'Google DeepMind'}]
    with patch.object(md, 'pick', return_value=items):
        return md.build('digest', {'posted': {}}, now)


class DigestPageTest(unittest.TestCase):
    """朝のまとめのリンク先（号のページ）・添える画像・見出しの短縮"""

    def test_headline_is_never_just_the_company_name(self):
        title = 'Google DeepMind、言葉で遊べる世界を作る新AI「Genie 4」を発表 操作も自由自在'
        got = md.one_line({'title': title, 'channel': 'Google DeepMind', 'tags': ['Google DeepMind']})
        self.assertNotEqual(got, 'Google DeepMind')
        self.assertEqual(got, 'Google DeepMind、新AI「Genie 4」を発表')

    def test_build_links_to_the_edition_page_with_utm_and_image(self):
        p = digest_post()
        self.assertEqual(p['link'], 'https://ai-tech-express.jp/digest/20261030')
        self.assertEqual(p['image'], f'https://ai-tech-express.jp/digest/20261030/share.png?v={md.dp.image_rev()}')
        # 2026-11-03 ルール1: X の本文にはリンクを入れない（号のページのリンクは返信。x_input）
        self.assertNotIn('https://', p['x_text'])
        reply = md.x_input('x', p, {'mode': 'shareNow'})['metadata']['twitter']['thread'][1]['text']
        self.assertIn('https://ai-tech-express.jp/digest/20261030?utm_source=x&utm_medium=social&utm_campaign=digest', reply)
        self.assertIn('・Google DeepMind、新AI「Genie 4」を発表', p['text'])
        self.assertEqual(p['edition']['id'], '20261030')
        self.assertEqual([i['headline'] for i in p['edition']['items']], ['NVIDIAが医療向けAI発表', 'Google DeepMind、新AI「Genie 4」を発表'])
        self.assertEqual(p['text'].splitlines()[0], '今日のAIニュース2本（2026/10/30）')

    def test_threads_post_has_image_and_edition_link_card(self):
        p = digest_post()
        data = md.threads_input('ch', p, {'mode': 'shareNow'})
        thread = data['metadata']['threads']['thread']
        self.assertEqual(thread[0]['assets'], [{'image': {'url': p['image']}}])
        # 2026-11-03 ルール1: Threads は本文の最後に号のページのリンク。返信は作らない
        self.assertEqual(len(thread), 1)
        self.assertIn('https://ai-tech-express.jp/digest/20261030?utm_source=threads&utm_medium=social&utm_campaign=digest', thread[0]['text'])
        self.assertEqual(data['text'], thread[0]['text'])
        self.assertEqual(md.x_input('x', p, {'mode': 'shareNow'})['assets'], [{'image': {'url': p['image']}}])

    def test_prepare_falls_back_to_news_without_image(self):
        p = digest_post()
        with tempfile.TemporaryDirectory() as tmp, patch.object(md.dp, 'DIGESTS_DIR', Path(tmp)), \
                patch.object(md.dp, 'design', return_value='A'), patch.object(md.dp, 'publish', return_value=False) as publish, \
                patch.object(md.dp, 'wait_live') as wait:
            ready = md.prepare_page(p)
            self.assertTrue((Path(tmp) / '20261030.json').exists())
        publish.assert_called_once()
        wait.assert_not_called()
        self.assertEqual(ready['link'], 'https://ai-tech-express.jp/news')
        self.assertEqual(ready['image'], '')
        self.assertNotIn('/digest/', ready['x_text'])
        thread = md.threads_input('ch', ready, {'mode': 'shareNow'})['metadata']['threads']['thread']
        self.assertEqual(thread[0]['assets'], [])
        # /news へのリンクは記事ではないので、門番（sns_rules）が投稿を止める（号のページを出せない日は朝のまとめを出さない）
        import sns_rules
        out = sns_rules.check_post('threads', thread[0]['text'], [], md.gate_article(ready), kind='digest')
        self.assertTrue(any('号のページ' in v for v in out))

    def test_prepare_keeps_edition_when_live(self):
        p = digest_post()
        with tempfile.TemporaryDirectory() as tmp, patch.object(md.dp, 'DIGESTS_DIR', Path(tmp)), \
                patch.object(md.dp, 'design', return_value='A'), patch.object(md.dp, 'publish', return_value=True), \
                patch.object(md.dp, 'wait_live', return_value=True) as wait, patch.object(md.dp, 'ai_background') as bg:
            ready = md.prepare_page(p)
        bg.assert_not_called()  # 案A では AI の背景を作らない
        self.assertEqual(wait.call_args_list[0].args[0], p['image'])
        self.assertEqual(ready['link'], p['link'])
        self.assertEqual(ready['image'], p['image'])

    def test_design_c_makes_one_background_per_edition(self):
        p = digest_post()
        with tempfile.TemporaryDirectory() as tmp, patch.object(md.dp, 'DIGESTS_DIR', Path(tmp)), \
                patch.object(md.dp, 'design', return_value='C'), patch.object(md.dp, 'publish', return_value=True), \
                patch.object(md.dp, 'wait_live', return_value=True), \
                patch.object(md.dp, 'ai_background', return_value='https://ai-tech-express.jp/img/digest-20261030-bg-1.jpg') as bg:
            ready = md.prepare_page(p)
            again = md.prepare_page(ready)
        self.assertEqual(bg.call_count, 1)
        self.assertEqual(again['edition']['aiImage'], 'https://ai-tech-express.jp/img/digest-20261030-bg-1.jpg')

    def test_rejected_image_post_is_recreated_without_image_once(self):
        p = digest_post()
        payload = md.threads_input('ch', p, {'mode': 'shareNow'})
        with patch.object(md.tp, 'create_post', side_effect=[RuntimeError('Buffer: invalid image'), 'ok']) as create:
            self.assertEqual(md.create_with_fallback('k', payload), 'ok')
        self.assertEqual(create.call_args_list[1].args[1]['metadata']['threads']['thread'][0]['assets'], [])
        # 通信エラー（受理不明）は作り直さない
        with patch.object(md.tp, 'create_post', side_effect=TimeoutError()) as create:
            with self.assertRaises(TimeoutError):
                md.create_with_fallback('k', payload)
            self.assertEqual(create.call_count, 1)

    def test_merge_restores_edition_file(self):
        p = digest_post()
        entry = {'style': 'digest', 'postPlan': p}
        with tempfile.TemporaryDirectory() as tmp, patch.object(md.dp, 'DIGESTS_DIR', Path(tmp)):
            md.restore_edition(entry)
            saved = json.loads((Path(tmp) / '20261030.json').read_text('utf-8'))
        self.assertEqual(saved['items'][1]['headline'], 'Google DeepMind、新AI「Genie 4」を発表')

    def test_bluesky_links_to_edition_page(self):
        p = digest_post()
        entry = {'style': 'digest', 'items': [], 'postPlan': p}
        got = bp.plan('digest-20261030', entry)
        self.assertEqual(got['link'], 'https://ai-tech-express.jp/digest/20261030?utm_source=bluesky&utm_medium=social&utm_campaign=digest&utm_content=digest_body')
        self.assertEqual(got['images'][0], f'https://ai-tech-express.jp/digest/20261030/share.png?v={md.dp.image_rev()}')
        old = bp.plan('digest-20261029', {'style': 'digest', 'items': [], 'postPlan': {**p, 'link': 'https://ai-tech-express.jp/news'}})
        self.assertTrue(old['link'].startswith('https://ai-tech-express.jp/news?'))


if __name__=='__main__':unittest.main()
