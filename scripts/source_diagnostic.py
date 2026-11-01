#!/usr/bin/env python3
"""Read-only feed probe using the same selection and state as article generation."""
import json
from pathlib import Path

import feeds

ROOT = Path(__file__).resolve().parent.parent


def main():
    state = json.loads((ROOT / 'content/state.json').read_text('utf-8'))
    for name, sources in [('ai', feeds.FEEDS), ('broad', feeds.BROAD_FEEDS)]:
        report = {}
        selected = feeds.collect(3, state, sources, report=report)
        print(f'[source-diagnostic] {name} {json.dumps(feeds.report_totals(report), sort_keys=True)}')
        # No feed titles or provider error bodies: only operational counts.
        print(f'[source-diagnostic] {name} selected_before_retry_limits={len(selected)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
