"""scripts/rivals.py：製品の記事の「同じクラスの製品と比べると？」の検査（ネットワーク・LLM なし。fixtures のページと決まった答え）。"""

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import article_charts  # noqa: E402
import calc_facts  # noqa: E402
import quality  # noqa: E402
import rivals  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "rivals"
FX = {"rate": 157.9, "date": "2026-11-02", "source": "ECB参照レート", "sourceUrl": "https://example.org/fx"}

ARTICLE = {
    "slug": "test-rx1",
    "sourceType": "web",
    "title": "テスト社が初の高級電動アシストマウンテンバイク「テスト自転車 RX1」を公開",
    "tags": ["自転車", "新製品"],
    "keywords": ["e-MTB"],
    "lede": "テスト社は電動アシストマウンテンバイクを公開しました。",
    "sourceUrl": "https://www.test-maker.example/news/rx1.html",
    "sourceName": "テスト社ニュースリリース",
    "generatedAt": "2026-11-02T15:31:56+00:00",
    "body": (
        "## 概要\n\nテスト自転車 RX1 が公開されました。\n\n"
        "## 数字で見ると？\n\n| 項目 | 数値 | 出典 |\n|---|---|---|\n| 車体重量 | 約24kg | テスト社 |\n"
        "| ドライブユニット重量 | 2.3kg | テスト社 |\n| 最大トルク | 100Nm | テスト社 |\n| 価格 | 言及なし | テスト社 |\n\n"
        "## なぜ今？\n\n理由です。\n\n## まとめ\n\nまとめです。\n"
    ),
    "charts": [],
    "factSources": [{"label": "料金", "name": "前からある出典", "url": "https://example.org/a", "checkedAt": "2026-11-01"}],
}

A_URL = "https://www.alpha-bikes.example/jp/ja/alpha-trail-4-comp/p/1"
B_URL = "https://www.beta-motor.co.jp/ypj/beta-mt-pro/spec.html"
C_URL = "https://www.gamma-bikes.example/jp/ja/gamma-e/"


def catalog():
    cat = copy.deepcopy(rivals.load_catalog())
    cat["categories"]["emtb"]["rivals"] = [
        {"name": "Gamma E-Trail", "maker": "Gamma", "url": C_URL},
        {"name": "Alpha Trail 4 Comp", "maker": "Alpha", "url": A_URL},
        {"name": "BETA-MT Pro", "maker": "Beta", "url": B_URL},
    ]
    return cat


PAGES = {
    A_URL: (FIX / "rival_a.html").read_bytes(),
    B_URL: (FIX / "rival_b.html").read_bytes(),
    C_URL: (FIX / "challenge.html").read_bytes(),
}


def fake_fetch(url, timeout=0, accept="", token=""):
    if url.endswith("/robots.txt"):
        raise OSError("no robots")
    if url in PAGES:
        return PAGES[url], url
    raise OSError("HTTP Error 404: Not Found")


def fetcher(url):
    if url == ARTICLE["sourceUrl"]:
        return {"text": (FIX / "self_release.txt").read_text("utf-8"), "title": "", "final_url": url}
    with mock.patch.object(rivals.feeds, "fetch", fake_fetch):
        rivals._robots.clear()
        return rivals.fetch_official(url)


def ask(system, prompt, schema):
    if system == rivals.CLASSIFY_SYSTEM:
        return {"is_product": True, "category": "emtb", "product_name": "テスト自転車 RX1", "maker_en": "Test",
                "rivals": [
                    {"name": "Delta Shop Bike", "maker_en": "Delta", "official_url": "https://www.big-retailer.example/delta"},
                    {"name": "Alpha Trail 5", "maker_en": "Alpha", "official_url": "https://www.alpha-bikes.example/jp/ja/alpha-trail-5/"},
                ]}
    if "テスト自転車 RX1" in prompt and "# ページ（https://www.test-maker.example" in prompt:
        return {"product_name_on_page": "テスト自転車 RX1", "fields": [
            {"key": "weight", "value": "約24kg", "quote": "約24kgの軽量な車体に独自のカーボンフレームを採用しています。"},
            {"key": "weight", "value": "2.3kg", "quote": "2.3kgの軽量ユニット"},
            {"key": "torque", "value": "100Nm", "quote": "最大トルク：100Nm、最大出力：750W"},
            {"key": "power", "value": "750W", "quote": "最大出力：750W"},
            {"key": "battery", "value": "720Wh", "quote": "バッテリー容量：720Whを実現しました。"},
            {"key": "fork", "value": "160mm", "quote": "フロントサスペンションは160mmストロークのフォークを採用。"},
            {"key": "wheels", "value": "前29インチ・後27.5インチ", "quote": "ホイールは前29インチ、後27.5インチです。"},
        ]}
    if "Alpha Trail 4 Comp" in prompt:
        return {"product_name_on_page": "Alpha Trail 4 Comp", "fields": [
            {"key": "weight", "value": "24.41kg（S4サイズ）", "quote": "参考重量 24.41 kg (53 lb, 13.0 oz)"},
            {"key": "motor", "value": "Alpha 3.1", "quote": "Alpha 3.1 Motor, 105Nm torque, 810W power"},
            {"key": "torque", "value": "105Nm", "quote": "Alpha 3.1 Motor, 105Nm torque, 810W power"},
            {"key": "power", "value": "810W", "quote": "トルク105Nm、パワー810W、バッテリー容量840Wh"},
            {"key": "battery", "value": "840Wh", "quote": "840Wh battery"},
            {"key": "fork", "value": "160mm", "quote": "FOX 36, 160mm travel"},
        ], "price": {"amount": 1320000, "quote": "¥1,320,000"}}
    if "BETA-MT Pro" in prompt:
        return {"product_name_on_page": "BETA-MT Pro", "fields": [
            {"key": "weight", "value": "23.3〜23.7kg（S〜L）", "quote": "車両重量 23.7kg 23.5kg 23.3kg"},
            {"key": "torque", "value": "85Nm", "quote": "最大トルク85Nm"},  # ページに無い（作り話）
            {"key": "power", "value": "250W", "quote": "定格出力 250W"},  # 定格は最大出力ではない
            {"key": "battery", "value": "36V-13.1Ah", "quote": "電圧／容量 36V-13.1Ah"},
            {"key": "fork", "value": "160mm", "quote": "フォーク（フロントサスペンション） 160mm"},
            {"key": "wheels", "value": "27.5インチ", "quote": "タイヤサイズ 前後27.5×2.6"},
        ], "price": {"amount": 748000, "quote": "748,000円 [消費税10％含む]"}}
    return {"product_name_on_page": "", "fields": []}


def build(article=None):
    logs = []
    data, why = rivals.build(copy.deepcopy(article or ARTICLE), catalog=catalog(), ask=ask, fetcher=fetcher, fx=FX,
                             log_fn=logs.append)
    return data, why, logs


class CheckValue(unittest.TestCase):
    page = "参考重量 24.41 kg (53 lb, 13.0 oz) 計測サイズ S4 定格出力 250W 2.3kgの軽量ユニット"
    defs = rivals.load_catalog()["fields"]

    def test_accepts_value_backed_by_page(self):
        self.assertIsNone(rivals.check_value("weight", self.defs["weight"], "24.41kg", "参考重量 24.41 kg", self.page))

    def test_rejects_quote_not_on_page(self):
        self.assertIn("ページに無い", rivals.check_value("weight", self.defs["weight"], "22kg", "参考重量 22 kg", self.page))

    def test_rejects_number_missing_from_quote(self):
        self.assertIn("根拠の文に無い", rivals.check_value("weight", self.defs["weight"], "25kg", "参考重量 24.41 kg", self.page))

    def test_rejects_wrong_unit_and_rated_power_and_part_weight(self):
        self.assertIn("単位", rivals.check_value("torque", self.defs["torque"], "24.41", "参考重量 24.41 kg", self.page))
        self.assertIn("定格", rivals.check_value("power", self.defs["power"], "250W", "定格出力 250W", self.page))
        self.assertIn("部品", rivals.check_value("weight", self.defs["weight"], "2.3kg", "2.3kgの軽量ユニット", self.page))

    def test_name_field_rejects_bare_quantity(self):
        self.assertIn("数字だけ", rivals.check_value("motor", self.defs["motor"], "2.3kg", "2.3kgの軽量ユニット", self.page))
        art = copy.deepcopy(ARTICLE)
        specs = rivals.table_specs(art, rivals.field_defs(rivals.load_catalog(), "emtb"))
        self.assertNotIn("motor", specs)  # 「ドライブユニット重量 2.3kg」はモーターの名前ではない

    def test_quote_recovered_from_page_near_label(self):
        page = "寸法 全長1,975mm 車両重量23.7kg23.5kg23.3kg 性能 一充電走行距離73km"
        raw = {"fields": [{"key": "weight", "value": "23.3〜23.7kg", "quote": "車両重量：23.7kg／23.3kg（L／S）"}]}
        got = rivals.verify(raw, page, [("weight", self.defs["weight"])], "https://x.example", "2026-11-03", log_fn=lambda m: None)
        self.assertEqual(got["weight"]["quote"], "車両重量23.7kg23.5kg23.3kg")
        raw = {"fields": [{"key": "weight", "value": "21kg", "quote": "車両重量 21kg"}]}
        self.assertEqual(rivals.verify(raw, page, [("weight", self.defs["weight"])], "u", "d", log_fn=lambda m: None), {})

    def test_mixed_range_is_rejected(self):
        page = "重さ 4.7g（イヤホン単体） 65.0g（ケース込み）"
        self.assertIn("範囲が広すぎる", rivals.check_value("weight", self.defs["weight"], "4.7〜65.0g", page, page))

    def test_w_does_not_match_wh(self):
        self.assertFalse(rivals._has_unit("840Wh", ["kW", "W"]))
        self.assertTrue(rivals._has_unit("810W", ["kW", "W"]))


class Price(unittest.TestCase):
    def test_currency_tax_region(self):
        p = rivals.check_price(748000, "748,000円 [消費税10％含む]", "価格 748,000円 [消費税10％含む]", B_URL, "2026-11-03")
        self.assertEqual((p["currency"], p["tax"], p["region"]), ("JPY", "税込", "日本"))
        self.assertEqual(p["display"], "748,000円（日本・税込）")

    def test_dollar_only_when_region_is_us(self):
        page = "$7,999 MSRP"
        self.assertIsNone(rivals.check_price(7999, "$7,999", page, "https://example.com/ca/en/bike", "2026-11-03"))
        p = rivals.check_price(7999, "$7,999", page, "https://example.com/us/en/bike", "2026-11-03")
        self.assertEqual(p["currency"], "USD")

    def test_amount_must_be_in_quote(self):
        self.assertIsNone(rivals.check_price(700000, "748,000円", "748,000円", B_URL, "2026-11-03"))


class Numeric(unittest.TestCase):
    units = ["kg", "g"]

    def test_parse(self):
        self.assertEqual(rivals.numeric("約24kg", self.units), {"lo": 24.0, "hi": 24.0, "unit": "kg", "approx": True, "range": False, "factor": 1, "shown": "kg"})
        n = rivals.numeric("23.3〜23.7kg（S〜L）", self.units)
        self.assertEqual((n["lo"], n["hi"], n["range"]), (23.3, 23.7, True))
        self.assertEqual(rivals.numeric("25.70 kg (S4)", self.units)["lo"], 25.7)
        self.assertIsNone(rivals.numeric("36V-13.1Ah", ["kWh", "Wh", "mAh", "Ah"]))
        self.assertIsNone(rivals.numeric("前29インチ・後27.5インチ", ["インチ"]))

    def test_spec_difference_formula(self):
        c = calc_facts.spec_difference("A", 24, "B", 25.7, "kg")
        self.assertEqual(c.values, [1.7])
        self.assertIn("Bの25.7kg − Aの24.0kg = 1.7kg", c.text)


class Candidates(unittest.TestCase):
    def test_retailer_urls_and_same_maker_are_dropped(self):
        cat = catalog()
        self.assertFalse(rivals.official_url_ok("https://www.amazon.co.jp/dp/X", "Specialized", cat))
        self.assertTrue(rivals.official_url_ok("https://www.specialized.com/jp/ja/x/p/1", "Specialized", cat))
        self.assertTrue(rivals.official_url_ok("https://www.alpha-bikes.example/x", "Alpha", cat))
        info = {"product_name": "Honda RN0", "maker_en": "Honda", "rivals": [{"name": "Honda X", "maker_en": "Honda", "official_url": "https://global.honda/x"}]}
        names = [c["name"] for c in rivals.candidates(info, cat, "emtb", log_fn=lambda m: None)]
        self.assertNotIn("Honda X", names)


class Fetch(unittest.TestCase):
    def test_challenge_page_is_skipped_not_bypassed(self):
        with self.assertRaises(rivals.Skip) as ctx:
            fetcher(C_URL)
        self.assertIn("確認画面", str(ctx.exception))

    def test_robots_disallow(self):
        def robots(url, timeout=0, accept="", token=""):
            if url.endswith("/robots.txt"):
                return b"User-agent: *\nDisallow: /\n", url
            return PAGES[A_URL], url
        with mock.patch.object(rivals.feeds, "fetch", robots):
            rivals._robots.clear()
            with self.assertRaises(rivals.Skip):
                rivals.fetch_official(A_URL)
        rivals._robots.clear()


class Build(unittest.TestCase):
    def test_end_to_end(self):
        data, why, logs = build()
        self.assertEqual(why, "ok")
        names = [p["name"] for p in data["products"]]
        self.assertEqual(names, ["テスト自転車 RX1", "Alpha Trail 4 Comp", "BETA-MT Pro"])
        me, a, b = data["products"]
        self.assertEqual(me["specs"]["weight"]["display"], "約24kg")  # 部品（ユニット）の 2.3kg は入れない
        self.assertEqual(a["specs"]["weight"]["display"], "24.41kg")  # 添え書きの数字（S4）が根拠に無いので外した値
        self.assertNotIn("torque", b["specs"])  # ページに無い 85Nm は捨てる
        self.assertNotIn("power", b["specs"])  # 定格出力は最大出力に入れない
        self.assertEqual(b["specs"]["battery"]["display"], "36V-13.1Ah")  # 換算しない
        self.assertTrue(any("Gamma" in s["name"] for s in data["skipped"]))  # 確認画面のライバルは飛ばす
        self.assertTrue(all(s["url"] and s["quote"] and s["checkedAt"] for p in data["products"] for s in p["specs"].values()))
        # 比べてわかること（表の数字だけ）
        self.assertEqual(data["takeaway"][0], "重さ（約24kg）は比べた製品とほぼ同じです。")
        self.assertEqual(data["takeaway"][1], "最大トルク（100Nm）はAlpha Trail 4 Compより5Nm小さいです。")
        self.assertIn("テスト自転車 RX1の価格は、発表資料には書かれていません（未発表）", data["priceNote"])
        self.assertIn("1,320,000円（日本）", data["priceNote"])
        self.assertTrue(any("= 5Nm" in c["text"] for c in data["computed"]))

    def test_apply_section_chart_sources_and_gate(self):
        data, _, _ = build()
        art = rivals.apply(copy.deepcopy(ARTICLE), data, catalog())
        body = art["body"]
        self.assertLess(body.index("## 数字で見ると？"), body.index("## 同じクラスの製品と比べると？"))
        self.assertLess(body.index("## 同じクラスの製品と比べると？"), body.index("## なぜ今？"))
        self.assertIn("| 最大トルク | 100Nm | 105Nm | 公式に記載なし |", body)
        self.assertIn("| 価格（公式） | 未発表 | 1,320,000円（日本） | 748,000円（日本・税込） |", body)
        self.assertIn(f"[Alpha公式（日本）]({A_URL})", body)
        chart = next(c for c in art["charts"] if c["kind"] == "rivals")
        self.assertEqual([g["label"] for g in chart["groups"]], ["重さ", "最大トルク"])
        self.assertTrue(chart["groups"][0]["items"][0]["highlight"])
        self.assertEqual(len([s for s in art["factSources"] if s["label"] == "比較"]), 2)
        self.assertEqual(art["factSources"][0]["name"], "前からある出典")
        self.assertEqual(quality.rival_problems(art), [])
        # 2回目は差し替え（章が2つにならない）
        again = rivals.apply(copy.deepcopy(art), data, catalog())
        self.assertEqual(again["body"].count("## 同じクラスの製品と比べると？"), 1)
        self.assertEqual(len([c for c in again["charts"] if c["kind"] == "rivals"]), 1)

    def test_gate_catches_numbers_without_source(self):
        data, _, _ = build()
        art = rivals.apply(copy.deepcopy(ARTICLE), data, catalog())
        bad = copy.deepcopy(art)
        bad["body"] = bad["body"].replace("| 105Nm |", "| 110Nm |")
        self.assertTrue(any("110" in p for p in quality.rival_problems(bad)))
        bad = copy.deepcopy(art)
        bad["rivals"]["products"][1]["specs"]["torque"]["display"] = "120Nm"
        self.assertTrue(quality.rival_problems(bad))
        bad = copy.deepcopy(art)
        bad.pop("rivals")
        self.assertTrue(quality.rival_problems(bad))
        # 元記事の数字の照合（number_problems）は比較の章を見ない
        self.assertNotIn("1,320,000", quality._article_numbers_text(art))

    def test_too_few_rivals_or_not_a_product(self):
        def no_product(system, prompt, schema):
            return {"is_product": False, "category": "other", "product_name": "", "maker_en": "", "rivals": []}
        data, why = rivals.build(copy.deepcopy(ARTICLE), catalog=catalog(), ask=no_product, fetcher=fetcher, fx=FX, log_fn=lambda m: None)
        self.assertIsNone(data)
        self.assertIn("製品の記事ではない", why)
        cat = catalog()
        cat["categories"]["emtb"]["rivals"] = cat["categories"]["emtb"]["rivals"][:2]  # 確認画面＋1つだけ
        data, why = rivals.build(copy.deepcopy(ARTICLE), catalog=cat, ask=ask, fetcher=fetcher, fx=FX, log_fn=lambda m: None)
        self.assertIsNone(data)
        self.assertIn("1つだけ", why)

    def test_roundup_article_whose_title_lacks_the_product_is_skipped(self):
        art = copy.deepcopy(ARTICLE)
        art["title"] = "英Armの10月動向：433コアの自律走行ロボやスマホ向け新AIの全貌"
        data, why = rivals.build(art, catalog=catalog(), ask=ask, fetcher=fetcher, fx=FX, log_fn=lambda m: None)
        self.assertIsNone(data)
        self.assertIn("題名に無い", why)

    def test_usd_price_gets_labelled_yen_estimate(self):
        data, _, _ = build()
        data["products"][1]["specs"]["price"] = rivals.check_price(7999, "$7,999", "$7,999", "https://x.example/us/en/a", "2026-11-03")
        computed = []
        note = rivals.price_note(data, FX, computed)
        self.assertIn("$7,999（米国）", note)
        self.assertIn("1ドル=157.90円（2026-11-02）で約126万円の目安です（サイソク編集部の計算）", note)
        self.assertEqual(computed[0].values, [1263042.0])


class SelfAndTier(unittest.TestCase):
    def test_self_specs_from_official_page_when_source_is_thin(self):
        art = copy.deepcopy(ARTICLE)
        art["body"] = "## 概要\n\n使い方の記事です。\n\n## まとめ\n\nまとめ。\n"
        art["sourceUrl"] = "https://www.carrier.example/tips"
        art["sourceName"] = "販売店の記事"
        cat = catalog()
        cat["makers"]["Test"] = ["test-maker.example"]

        def fetch2(url):
            if url == art["sourceUrl"]:
                return {"text": "テスト自転車 RX1 の楽しみ方を紹介します。" * 20, "title": "", "final_url": url}
            if url == "https://www.test-maker.example/rx1/spec":
                return {"text": (FIX / "self_release.txt").read_text("utf-8"), "title": "テスト自転車 RX1", "final_url": url}
            return fetcher(url)

        def ask2(system, prompt, schema):
            if system == rivals.CLASSIFY_SYSTEM:
                return {**ask(system, prompt, schema), "official_url": "https://www.test-maker.example/rx1/spec"}
            if "# ページ（https://www.test-maker.example/rx1/spec）" in prompt:
                return ask(system, prompt.replace("https://www.test-maker.example/rx1/spec", "https://www.test-maker.example/news"), schema)
            if "# ページ（https://www.carrier.example" in prompt:
                return {"product_name_on_page": "", "fields": []}
            return ask(system, prompt, schema)

        data, why = rivals.build(art, catalog=cat, ask=ask2, fetcher=fetch2, fx=FX, log_fn=lambda m: None)
        self.assertEqual(why, "ok")
        me = data["products"][0]
        self.assertEqual(me["specs"]["torque"]["url"], "https://www.test-maker.example/rx1/spec")
        section = rivals.section_markdown(data, cat)
        self.assertIn("(https://www.test-maker.example/rx1/spec)", section)

    def test_same_tier_only_when_enough(self):
        cat = rivals.load_catalog()
        info = {"product_name": "Galaxy S26 Ultra", "maker_en": "Samsung", "tier": "high", "rivals": []}
        names = [c["name"] for c in rivals.candidates(info, cat, "smartphone", log_fn=lambda m: None)]
        self.assertEqual(names, ["iPhone 18 Pro", "Google Pixel 11 Pro"])
        info = {"product_name": "Galaxy A57", "maker_en": "Samsung", "tier": "entry", "rivals": []}
        names = [c["name"] for c in rivals.candidates(info, cat, "smartphone", log_fn=lambda m: None)]
        self.assertEqual(names, ["iPhone 17e", "Google Pixel 10a"])
        # iPad mini（中くらい）を、上位の大きなタブレットと比べない
        info = {"product_name": "iPad mini", "maker_en": "Apple", "tier": "mid", "rivals": []}
        self.assertEqual(rivals.candidates(info, cat, "tablet", log_fn=lambda m: None), [])

    def test_catalog_tier_wins_and_listing_pages_are_dropped(self):
        cat = rivals.load_catalog()
        info = {"product_name": "iPad mini (A20 Pro)", "maker_en": "Apple", "tier": "high", "rivals": [
            {"name": "Pixel 9 Pro Fold", "maker_en": "Google", "official_url": "https://store.google.com/jp/category/phones?hl=ja"}]}
        self.assertEqual(rivals.candidates(info, cat, "tablet", log_fn=lambda m: None), [])

    def test_joined_clauses_read_naturally(self):
        me = {"name": "A", "specs": {"weight": {"display": "201g"}}}
        rs = [{"name": "B", "specs": {"weight": {"display": "239g"}}}, {"name": "C", "specs": {"weight": {"display": "226g"}}}]
        field = rivals.load_catalog()["fields"]["weight"]
        text = rivals.compare_sentence(field, me, rs, "weight", [])
        self.assertEqual(text, "重さ（201g）はBより38g軽く、Cより25g軽いです。")

    def test_catalog_urls_are_official_domains(self):
        cat = rivals.load_catalog()
        for key, c in cat["categories"].items():
            for r in c.get("rivals") or []:
                self.assertTrue(rivals.official_url_ok(r["url"], r["maker"], cat), f"{key}: {r['name']} {r['url']}")
                self.assertIn(r.get("tier"), ("high", "mid", "entry", "any"), r["name"])


class GramsAndNames(unittest.TestCase):
    def test_gram_difference_is_shown_in_grams_and_names_with_numbers_pass_gate(self):
        cat = rivals.load_catalog()
        mk = lambda name, w, url: {"name": name, "maker": name, "self": name == "Phone 11 Pro", "url": url, "sourceName": "公式",
                                    "checkedAt": "2026-11-03",
                                    "specs": {"weight": {"display": w, "quote": f"重さ {w}", "url": url, "checkedAt": "2026-11-03"}}}
        data = {"category": "smartphone", "checkedAt": "2026-11-03", "products": [
            mk("Phone 11 Pro", "211g", "https://a.example/1"), mk("Other 18 Pro", "190g", "https://b.example/2"),
            mk("Third 8", "214g", "https://c.example/3")]}
        data["takeaway"], computed = rivals.takeaway(data, cat)
        self.assertIn("Other 18 Proより21g重い", data["takeaway"][0])
        self.assertIn("Third 8とほぼ同じ", data["takeaway"][0])
        data["priceNote"] = rivals.price_note(data, FX, computed)
        data["computed"] = [{"text": c.text, "values": c.values} for c in computed]
        art = rivals.apply({"body": "## 数字で見ると？\n\n表\n\n## まとめ\n", "charts": []}, data, cat)
        self.assertEqual(quality.rival_problems(art), [])

    def test_self_spec_url_needs_exact_name(self):
        cat = rivals.load_catalog()
        urls = rivals.self_spec_urls({"maker_en": "Google"}, "Google Pixel 11 Pro", cat)
        self.assertEqual(urls, ["https://store.google.com/jp/product/pixel_11_pro_specs?hl=ja"])


class Charts(unittest.TestCase):
    def test_article_charts_keeps_rivals_chart_and_skips_rivals_table(self):
        data, _, _ = build()
        art = rivals.apply(copy.deepcopy(ARTICLE), data, catalog())
        charts = article_charts.derive_charts(art)
        self.assertEqual([c["kind"] for c in charts], ["rivals"])


class Table(unittest.TestCase):
    def test_table_specs_fallback(self):
        fields = rivals.field_defs(rivals.load_catalog(), "emtb")
        specs = rivals.table_specs(ARTICLE, fields)
        self.assertEqual(specs["weight"]["display"], "約24kg")  # 「ドライブユニット重量」は重さに入れない
        self.assertEqual(specs["torque"]["display"], "100Nm")
        self.assertNotIn("price", specs)


if __name__ == "__main__":
    unittest.main()
