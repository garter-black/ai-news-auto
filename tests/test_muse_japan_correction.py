import json
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
META='https://about.fb.com/ja/news/2026/10/muse-personal-ai-agent-launches-in-japan/'
def load(name): return json.loads((ROOT/name).read_text(encoding='utf-8'))
class MuseCorrection(unittest.TestCase):
    def test_both_guides_identify_japan_and_link_primary_source(self):
        # 2026-11-03: Muse Wonder・OpenAI dot との比較のガイドは meta-muse-japan-availability にまとめた（台帳 W15）
        for name in ('meta-muse-japan-availability',):
            with self.subTest(name=name):
                a=load(f'content/howto/{name}.json')
                self.assertEqual(a['refreshMode'],'editorial')
                self.assertIn(META,[s['url'] for s in a['sources']])
                self.assertIn('10月23日',a['japan']['answer'])
                self.assertNotIn('日本での提供を今回確認した発表では確認できません',str(a))
    def test_muse_guides_are_merged_into_one(self):
        for old in ('meta-muse-wonder','meta-muse-openai-dot-compare'):
            self.assertFalse((ROOT/f'content/howto/{old}.json').exists(), old)
        config=(ROOT/'next.config.ts').read_text(encoding='utf-8')
        self.assertIn('"meta-muse-wonder": "meta-muse-japan-availability"',config)
        self.assertIn('"meta-muse-openai-dot-compare": "meta-muse-japan-availability"',config)
        merged=load('content/howto/meta-muse-japan-availability.json')
        self.assertEqual(merged['toolName'],'Meta Muse')
        text=json.dumps(merged,ensure_ascii=False)
        for word in ('Muse Wonder','Wonder Full','dot','10月23日'):
            self.assertIn(word,text)
        # 1つのガイドに見出し（toolName）が1つだけ。Muse 関係のガイドがほかに無い
        others=[p.name for p in (ROOT/'content/howto').glob('*.json') if 'muse' in p.name and p.name!='meta-muse-japan-availability.json']
        self.assertEqual(others,[])
        self.assertNotIn('記載がありません',text)
    def test_corrected_news_carries_visible_primary_source_and_status(self):
        for name in ('meta-muse-singapore-launch','meta-muse-ai-agent-app-launch','nvidia-meta-muse-partnership-expansion'):
            with self.subTest(name=name):
                # 取り下げた記事（content/retired.json。他人の動画の要約など）は確かめない
                if not (ROOT/f'content/articles/{name}.json').exists():
                    self.assertIn(name, load('content/retired.json')['articles'])
                    continue
                a=load(f'content/articles/{name}.json')
                self.assertEqual(a['verdict']['japan'],'ok')
                self.assertIn(META,a['body'])
                self.assertIn('編集部訂正',a['body'])
                self.assertNotIn('日本から正式に使えるようになるのは、対応を待つ必要があります',a.get('easy',''))
                self.assertNotIn('VPNの利用や米国アカウント',a['body'])
if __name__=='__main__': unittest.main()
