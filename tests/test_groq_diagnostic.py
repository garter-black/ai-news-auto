import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import groq_diagnostic as diag


class GroqDiagnosticTests(unittest.TestCase):
    def test_response_classification(self):
        self.assertEqual(diag.classify(403, 'unsupported_country'), 'region_restriction')
        self.assertEqual(diag.classify(403, 'organization is disabled'), 'organization_disabled')
        self.assertEqual(diag.classify(401, 'invalid api key'), 'authentication')
        self.assertEqual(diag.classify(403, 'arbitrary confidential content'), 'permission_or_policy')
        self.assertEqual(diag.classify(403, 'Cloudflare challenge-platform', 'text/html'), 'security_challenge')
        self.assertEqual(diag.classify(429, ''), 'rate_or_quota')


if __name__ == '__main__':
    unittest.main()
