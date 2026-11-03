"""検索に出す記事の判定が、IndexNow（scripts/indexnow.py）とサイト（lib/indexing.ts: サイトマップ・ニュース用サイトマップ・robots）で同じか。

1. lib/index-rules.json が scripts/indexnow.py・persona.py の今の基準から書き出したものと一致する
2. 実際の記事と例で、両方の noindex の理由が一致する（node と typescript が無い環境では 2 を飛ばす）
ネットワークは使わない。
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import indexnow  # noqa: E402

NODE_SCRIPT = r"""
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');
const filename = path.join(process.cwd(), 'lib/indexing.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, esModuleInterop: true}}).outputText;
const m = new Module(filename, module);
m.filename = filename;
m.paths = Module._nodeModulePaths(process.cwd());
m._compile(compiled, filename);
const items = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(items.map((a) => m.exports.noindexReason(a))));
"""

SAMPLES = [
    {"genreSlug": "business", "title": "財務省が国債金利を公表"},
    {"genreSlug": "public", "title": "道路の工事", "seoTitle": "補助金の申請方法"},
    {"genreSlug": "public", "title": "道路の工事"},
    {"genreSlug": "lifestyle", "title": "電気料金の値上げ"},
    {"genreSlug": "lifestyle", "title": "新作ラテ"},
    {"genreSlug": "tech-gadget", "title": "新しいスマホ"},
    {"genreSlug": "science", "title": "新しい顕微鏡", "tags": ["AI"]},
    {"genreSlug": "science", "title": "AIX という名前の装置"},
    {"genreSlug": "sports", "title": "ＮＢＡのウォリアーズが勝利"},
    {"genreSlug": "sports", "title": "大谷翔平のドジャース"},
    {"genreSlug": "sports", "title": "W杯で海外の選手が活躍"},
    {"genreSlug": "sports", "title": "全日本社会人選手権が閉幕"},
    {"genreSlug": "entertainment", "title": "新作ゲーム"},
    # エンタメはビジネス・テックの目線だけ（scripts/genre_focus.py。2026-11-03）
    {"genreSlug": "entertainment", "title": "カプコン、中間決算は増収増益　販売本数は前年比1.3倍"},
    {"genreSlug": "entertainment", "title": "Ｎｅｔｆｌｉｘ、広告つきプランの月額を890円に"},
    {"genreSlug": "entertainment", "title": "映画の予告編を公開　キャストも発表"},
    {"genreSlug": "entertainment", "title": "人気アニメのコラボグッズを発売、価格は1500円"},
    {"genreSlug": "investment", "title": "金融庁、NISAの手続きを簡素化"},
    {"genreSlug": "real-estate", "title": "公示地価が上昇"},
]


def _summary(article: dict, body_length: int, japan_value: bool) -> dict:
    keys = ("slug", "type", "title", "seoTitle", "tags", "genreSlug", "channelSlug", "sourceType", "indexable", "verdict")
    out = {k: article.get(k) for k in keys if article.get(k) is not None}
    out.setdefault("tags", [])
    out.setdefault("title", "")
    out.setdefault("genreSlug", "ai")
    out.setdefault("channelSlug", "")
    out.update(bodyLength=body_length, hasJapanValue=japan_value)
    return out


class IndexRulesTest(unittest.TestCase):
    def test_rules_json_is_up_to_date(self):
        self.assertFalse(
            indexnow.rules_drift(),
            "lib/index-rules.json が古い。python3 scripts/indexnow.py --export-rules で書き出す",
        )

    def test_python_and_site_agree(self):
        node = shutil.which("node")
        if not node or subprocess.run([node, "-e", "require.resolve('typescript')"], cwd=ROOT,
                                      capture_output=True).returncode != 0:
            self.skipTest("node / typescript が無い")
        articles, expected, summaries = [], [], []
        for path in sorted((ROOT / "content" / "articles").glob("*.json")):
            try:
                articles.append(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError):
                continue
        jp = "## 日本で使える？\n" + "a" * 2000
        for sample in SAMPLES:
            articles.append({**sample, "body": jp})
        for article in articles:
            if not isinstance(article, dict):
                continue
            article = {k: v for k, v in article.items() if k != "duplicateOf"}  # 重複は別の仕組み（索引側）で付く
            body = article.get("body", "")
            expected.append(indexnow.noindex_reason(article))
            summaries.append(_summary(article, len(body), bool(indexnow.JAPAN_VALUE.search(body))))
        out = subprocess.run([node, "-e", NODE_SCRIPT], cwd=ROOT, input=json.dumps(summaries), capture_output=True,
                             text=True, check=True)
        got = json.loads(out.stdout)
        diffs = [(s.get("slug") or s["title"], e, g) for s, e, g in zip(summaries, expected, got) if e != g]
        self.assertEqual(diffs, [], "IndexNow とサイトで noindex の理由が違う")


if __name__ == "__main__":
    unittest.main()
