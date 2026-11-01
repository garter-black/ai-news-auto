import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import llm


class FailoverTests(unittest.TestCase):
    def test_quota_and_forbidden_try_next_provider(self):
        schema = {'required': ['answer']}
        llm._exhausted.clear()

        def compatible(model, *args):
            if model.startswith('groq:'):
                raise RuntimeError('HTTP Error 403: Forbidden')
            return '{"answer":"ok"}'

        with patch.object(llm, 'models_for', return_value=['gemini-test', 'groq:test', 'cloudflare:test']), \
             patch.object(llm, '_call_gemini', side_effect=RuntimeError('429 RESOURCE_EXHAUSTED')), \
             patch.object(llm, '_call_openai_compatible', side_effect=compatible) as external, \
             patch.object(llm, '_record_usage'):
            result, model = llm.generate_json('system', 'prompt', schema, tier='bulk')
        self.assertEqual(result, {'answer': 'ok'})
        self.assertEqual(model, 'cloudflare:test')
        self.assertEqual(external.call_count, 2)
        self.assertIn('gemini-test', llm._exhausted)
        llm._exhausted.clear()


if __name__ == '__main__':
    unittest.main()
