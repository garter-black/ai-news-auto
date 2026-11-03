#!/usr/bin/env python3
"""ニュースの種類ごとに記事を組み立てる（2026-11-03 オーナー決定「かなり大事」。決まった章立てをやめる）。

これまで: どの記事も同じ章立て（概要／何が発表された？／料金は？／日本で使える？／ライバルと比べてどう？／自分に関係ある？／まとめ）。
  決算にも脆弱性にも「料金は？」が出て、材料の無い章は「元記事には書かれていません」で埋まっていた（直近227本で章の2割）。
これから: 元の資料（題名・本文・URL）と確かな事実の束からニュースの種類を1つ決め、その種類の「型」で書く。
  - 型 = 最初の2文に入れること・必ず入れる事実（資料にあれば）・書く順番の目安・書かないこと・長さの目安
  - 見出しは記事の中身の言葉で、1,000字に1つまで。短い記事は見出しなし。材料の無い章は作らない
  - 必ず入れる事実のうち、資料にあるのに本文に無いものは、作り直しの指摘にする（missing_facts）

使う場所:
  - scripts/web_articles.py（通常・速報・幅広い話題・話題のキーワード）: prompt_block() を章立ての代わりに渡す
  - scripts/main.py（動画の記事）・scripts/roundup.py（まとめ）・scripts/angle_articles.py: 同じ prompt_block()
  - 記事 JSON の newsType に種類を残す（表示・点検・あとの集計用）

  python3 scripts/news_types.py --slug apple-reports-q4-2026-results   # 今ある記事の種類を判定して表示
  python3 scripts/news_types.py --recent 200                           # 直近の記事の種類の内訳
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARTICLES_DIR = ROOT / "content" / "articles"


@dataclass(frozen=True)
class MustHave:
    """必ず入れる事実。source_re が資料に当たり、body_re が本文に当たらなければ「抜け」
    hard=True は作り直しの指摘にする（当たり方が確かなものだけ）。False は書くときのチェックリストだけ"""

    key: str
    label: str
    hint: str
    source_re: re.Pattern | None = None
    body_re: re.Pattern | None = None
    hard: bool = False


@dataclass(frozen=True)
class NewsType:
    key: str
    label: str
    lead: str          # リード（最初の1〜2文）に入れること
    flow: str          # 書く順番の目安（見出しの名前ではない）
    must: tuple[MustHave, ...]
    avoid: str         # 書かないこと・作らない章
    length: tuple[int, int]  # 本文の字数の目安（材料が少なければ下限より短くてよい）
    verdict: bool = False    # 「使えるか」の判定カードを出してよい種類か
    examples: tuple[str, ...] = field(default_factory=tuple)  # 見出しの例（その記事の中身の言葉）


def _re(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


MONEY = r"(?:\d[\d,.]*\s?(?:万|億|兆)?\s?(?:円|ドル|ユーロ|ポンド|元|ウォン)|[$€£¥]\s?\d[\d,.]*|\d[\d,.]*\s?(?:USD|EUR|JPY|dollars?)\b)"
DATE = r"(?:\d{1,2}月\d{1,2}日|\d{1,2}月(?:上旬|中旬|下旬)|\d{4}年\d{1,2}月|(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sep\.?|Sept\.?|Oct\.?|Nov\.?|Dec\.?)\s+\d{1,2})"
PCT = r"\d[\d.]*\s?(?:%|％|パーセント|ポイント)"

TYPES: dict[str, NewsType] = {}


def _add(t: NewsType) -> None:
    TYPES[t.key] = t


_add(NewsType(
    key="earnings",
    label="決算・業績",
    lead="売上高・利益の額と前年比、「過去最高」か「伸びが鈍った」か。会社が出した次の期の見通しがあれば2文目に。",
    flow="全体の数字（売上・利益・前年比）→ 事業別・製品別の数字 → 地域別（日本の数字があれば必ず）→ 経営陣の説明（誰の発言か）→ 次の期の見通し → まだ分からないこと",
    must=(
        MustHave("revenue", "売上高（収益）の額", "資料の額。表が「百万ドル」単位なら「575億8,300万ドル」のように億・万で書く",
                 _re(r"売上高|売上収益|営業収益|純売上|revenue|net sales"), _re(r"売上|収益|revenue"), True),
        MustHave("yoy", "前年（前年同期）との比較", "増減率か前年の額。計算済みの数字が無ければ資料の表記だけ",
                 _re(r"前年(?:同期|同四半期)?比|前年同期|year[- ]over[- ]year|year-ago|compared (?:to|with)|増収|減収|増益|減益|up \d+\s?(?:%|percent)|down \d+\s?(?:%|percent)"),
                 _re(r"前年|増収|減収|増益|減益|[0-9０-９.]+\s?(?:%|％)\s?(?:増|減)|(?:増|減)(?:えて|り|少|加)"), True),
        MustHave("profit", "利益（営業利益・純利益・1株利益）", "資料にある利益の指標",
                 _re(r"営業利益|純利益|経常利益|当期利益|net income|operating income|earnings per share|EPS|diluted"), _re(r"利益|EPS|1株"), True),
        MustHave("segment", "事業別・製品別の数字", "資料に表があれば主な2〜3項目",
                 _re(r"セグメント|事業別|部門別|製品別|segment|by category|product summary|iPhone|Mac|iPad|Services|Wearables"), None),
        MustHave("region", "地域別の数字（日本の数字があれば必ず）", "日本・米州・中国など",
                 _re(r"地域別|日本|Japan|Greater China|中華圏|Americas|Europe|欧州|米州"), None),
        MustHave("guidance", "会社が出した次の期の見通し", "見通しの数字と期間（会社の発表として）",
                 _re(r"見通し|業績予想|通期予想|ガイダンス|outlook|guidance|expects?\s+(?:revenue|growth|sales)|forecast"),
                 _re(r"見通し|予想|見込|計画|ガイダンス"), True),
        MustHave("voice", "経営陣の説明（誰の発言か）", "CEO・CFO の発言は「〇〇氏は…と説明しました」",
                 _re(r"CEO|CFO|社長|最高経営責任者|最高財務責任者|said|says|コメント"), None),
    ),
    avoid="料金・日本で使えるか・ライバルとの比較・「もう買える？」の章は作らない。会社の宣伝の言葉（「最も変革的」など）を地の文で書かない。",
    length=(1600, 3000),
    examples=("iPhoneは17%増、iPadは減収", "10〜12月期は7〜10%増を見込む"),
))

_add(NewsType(
    key="security",
    label="脆弱性・障害・欠陥",
    lead="何が・どれほど危ないか（深刻度の点数や評価）と、直った版・対策。悪用が確認されているかが分かれば1〜2文目に。",
    flow="何が起きたか（影響を受ける製品・版）→ 深刻度（CVSS などの点数・評価）→ 悪用の有無 → 直し方（更新で済むか・作り直しが要るか・自動更新か）→ すぐ直せないときの回避策 → まだ分からないこと",
    must=(
        MustHave("scope", "影響を受ける製品・サービス", "製品名と範囲",
                 _re(r"影響を受ける|対象(?:製品|となる)|affected|impacted|vulnerable"), _re(r"影響|対象")),
        MustHave("severity", "深刻度（CVSS などの点数・評価）", "点数と評価の言葉。資料の表記どおり",
                 _re(r"CVSS|深刻度|Severity|Critical|緊急|重要度"), _re(r"CVSS|深刻|緊急|重要度|Critical|High|危険"), True),
        MustHave("versions", "対象のバージョン（版）", "「〇〇より前の版」など資料どおり",
                 _re(r"(?:バージョン|version|v)\s?\d+(?:\.\d+)+|\d+\.\d+(?:\.\d+)+|より前|以前の|未満|prior to|earlier|before \d"),
                 _re(r"\d+\.\d+|バージョン|版"), True),
        MustHave("fix", "直し方（修正版・更新・自動更新か）", "修正版の番号、更新の方法、自動で直るか",
                 _re(r"修正|アップデート|パッチ|更新|patch|update|fixed|upgrade|mitigat"), _re(r"修正|アップデート|更新|パッチ"), True),
        MustHave("exploit", "悪用の有無", "「悪用は確認されていない」などの公式の表現",
                 _re(r"悪用|攻撃(?:が|を)?確認|exploit|in the wild|actively"), _re(r"悪用|攻撃"), True),
        MustHave("workaround", "すぐ直せないときの回避策", "資料にある回避策だけ",
                 _re(r"回避策|緩和策|workaround|mitigation|無効化"), _re(r"回避|緩和|無効")),
    ),
    avoid="価格・「もう使える？」「日本で使える？」の章は作らない。名前（「ブロトリ」など）から想像した説明は書かない。攻撃の手順は書かない。",
    length=(1600, 2400),
    examples=("1.1.0より前の版に影響、修正版は1.2.0", "悪用はまだ確認されていない"),
))

_add(NewsType(
    key="product",
    label="新製品・新サービス・機能",
    lead="何が新しいか（前の版・今までとの違い）と、価格・提供開始日。日本で使えるか・買えるかが分かれば2文目に。",
    flow="何が新しいか → 価格と発売日・提供範囲（日本での提供）→ 主な仕様・できること → 前の版・競合との違い（数字があるときだけ）→ 今の利用者・持ち主への扱い（後付けできるか・追加料金か）→ まだ分からないこと",
    must=(
        MustHave("price", "価格・料金", "資料の表記どおり（税込か税別かも）。無ければ「価格は未発表」と1回だけ",
                 _re(MONEY + r"|税込|税別|価格|料金|pricing|\bprice"), _re(r"円|ドル|ユーロ|価格|料金|無料|有料|未発表|未定"), True),
        MustHave("date", "発売日・提供開始日", "日付と、段階的な提供ならその順番",
                 _re(DATE + r"|発売|提供開始|販売開始|受注開始|available (?:today|now|from|starting)|rolling out|generally available"),
                 _re(r"\d{1,2}月|発売|提供|開始|予定|今日から|順次"), True),
        MustHave("japan", "日本での提供・日本語対応", "日本で使える・買えるか。資料に無ければ書かない",
                 _re(r"日本|国内|Japan|日本語|全世界|worldwide|globally"), _re(r"日本|国内|世界")),
        MustHave("spec", "主な仕様・できること", "数字のある仕様を2〜3つ",
                 _re(r"\d+\s?(?:mm|kg|g|インチ|GB|TB|時間|km|W|Wh|mAh|万画素|コア|トークン|tokens?|パラメータ)"), None),
        MustHave("diff", "前の版・競合との違い（数字つき）", "比べる数字が資料・確かな事実の束にあるときだけ",
                 _re(r"従来|前モデル|前世代|旧型|これまで|compared|than|previous|比べ"), None),
        MustHave("owners", "今の利用者・持ち主への扱い", "後付けできるか、追加料金か、自動で使えるか",
                 _re(r"既存|後付け|アップグレード|既に|現在の(?:ユーザー|利用者|契約者)|existing (?:users|customers)"), None),
    ),
    avoid="材料の無い「料金は？」「日本で使える？」「ライバルと比べてどう？」の章は作らない。一部改良を「新型」と書かない。前からある機能を「新たに」と書かない。",
    length=(1600, 2600),
    verdict=True,
    examples=("ハイブリッドに入門グレード、価格は570万円から", "丸目ライトはメーカーオプション、バンパーは販売店で"),
))

_add(NewsType(
    key="pricing",
    label="料金・プランの変更",
    lead="何が・いくらに・いつから変わるか、今の契約者はどうなるか。",
    flow="変更の中身（前と今の料金・上限）→ いつから → 今の契約者への扱い → 単位の違い（週・5時間・月）など誤解しやすい点 → 円の目安（計算済みの数字があるときだけ）→ まだ分からないこと",
    must=(
        MustHave("amounts", "前と今の料金・上限", "前と今を並べる（表にしてよい）",
                 _re(MONEY), _re(MONEY + r"|無料|据え置き"), True),
        MustHave("start", "いつから変わるか", "日付。段階的ならその順番",
                 _re(DATE + r"|から|starting|effective|beginning|開始"), _re(r"\d{1,2}月|から|開始|順次|すでに"), True),
        MustHave("existing", "今の契約者への扱い", "自動で移るか、手続きが要るか",
                 _re(r"既存|現在の|今の契約|existing|current (?:subscribers|plan|customers)|自動的に"), _re(r"既存|今の|現在の|契約者|自動")),
        MustHave("units", "単位の違い（週・5時間・月など）", "数字の単位が変わったときは1文で説明",
                 _re(r"per week|weekly|per month|5[- ]hour|週|5時間|月ごと"), None),
    ),
    avoid="ライバルとの比較は、確かな事実の束に相手の料金があるときだけ。料金表を本文に写さない（プログラムが差し込む）。",
    length=(1600, 2400),
    verdict=True,
    examples=("月200ドルは据え置き、5時間ごとの上限はなくなる",),
))

_add(NewsType(
    key="deal",
    label="提携・買収・出資",
    lead="誰が誰と・何を（買収・出資・提携）・いくらで・いつ。",
    flow="取引の中身（相手・形・金額・比率）→ 時期（発表日・完了予定・当局の承認）→ 発表者が説明した狙い（誰の説明か）→ 利用者・取引先への影響（資料にあるときだけ）→ まだ分からないこと",
    must=(
        MustHave("party", "相手の会社と取引の形", "買収・出資・提携のどれか",
                 _re(r"買収|出資|提携|合併|協業|acqui|merger|partner|invest|stake"), _re(r"買収|出資|提携|合併|協業|連携")),
        MustHave("amount", "金額・出資比率", "資料の表記どおり",
                 _re(MONEY + r"|" + PCT), _re(MONEY + r"|" + PCT + r"|非公表|明らかにしていない"), True),
        MustHave("timing", "完了の予定・承認", "完了予定の時期、規制当局の承認が要るか",
                 _re(r"完了|クロージング|close|承認|approval|予定|expected to"), _re(r"完了|承認|予定|見込")),
    ),
    avoid="料金・日本で使えるか・「もう使える？」の章は作らない。株価や「買い」の助言は書かない。",
    length=(1600, 2400),
    examples=("買収額は約1兆円、完了は2027年前半の見込み",),
))

_add(NewsType(
    key="filing",
    label="上場・届出・申請書類",
    lead="前の版から何が増えた・変わったか（新しい数字・条件）。初めての提出なら一番大事な数字。",
    flow="今回の書類で新しく分かったこと → 主な数字（売上・損失・株数・議決権など）→ 注意書き（リスク・取り消せる条件）→ 今後の手続きと日程 → まだ決まっていないこと",
    must=(
        MustHave("delta", "前の版・前回からの変更点", "「前回の書類では…、今回は…」",
                 _re(r"修正|改訂|amend|変更|追加|updated"), _re(r"修正|変更|追加|新た|今回|前回")),
        MustHave("numbers", "書類にある主な数字", "資料の表記どおり",
                 _re(MONEY + r"|" + PCT + r"|株"), _re(MONEY + r"|" + PCT + r"|株"), True),
    ),
    avoid="料金・日本で使えるか・判定カードは出さない。前の記事と同じ事実だけで記事を作らない。株を買うべきかは書かない。",
    length=(1600, 2400),
    examples=("創業者側の議決権は50.1%、売買の記号は「ANT」",),
))

_add(NewsType(
    key="policy",
    label="規制・制度・行政",
    lead="誰が・いくら（何が）・いつから・いつまで。決定か案（検討中・意見募集）かも1文目か2文目に。",
    flow="何が決まった・始まったか（決定か案か）→ 対象になる人・ならない人 → 金額・税率・条件（3つ以上なら金額・期限・対象の早見表。最後の列は出典。公式の数字と計算済みの数字だけ）→ いつから・締め切り → 自分の場合の確かめ方（資料にある書類・計算ページ・窓口。番号つきの手順）→ まだ決まっていないこと",
    must=(
        MustHave("target", "対象者（対象にならない人も）", "所得・年齢・地域・業種などの条件は資料どおり",
                 _re(r"対象|eligible|適用"), _re(r"対象|適用"), True),
        MustHave("amount", "金額・税率・上限", "資料の表記どおり",
                 _re(MONEY + r"|" + PCT + r"|税率"), _re(MONEY + r"|" + PCT + r"|税率"), True),
        MustHave("start", "開始日・期限・締め切り", "日付つき",
                 _re(DATE + r"|から|まで|期限|締切|締め切り|受付期間|申請期間|施行"), _re(r"\d{1,2}月|から|まで|期限|締め|施行"), True),
        MustHave("procedure", "手続きと窓口", "申し込み先・必要な書類。資料にあるものだけ",
                 _re(r"申請|申し込|手続|窓口|届出|申告|オンライン"), _re(r"申請|申し込|手続|窓口|届出|申告")),
        MustHave("status", "決定か案か", "閣議決定・施行・検討中・意見募集中など",
                 _re(r"決定|施行|成立|検討|案|意見募集|パブリックコメント|方針"), _re(r"決定|施行|成立|検討|案|方針")),
    ),
    avoid="ライバルとの比較・「もう使える？」の章は作らない。政党・選挙の評価は書かない。",
    length=(1600, 2600),
    verdict=True,
    examples=("中小企業は最大750万円、申請は12月26日まで",),
))

_add(NewsType(
    key="research",
    label="調査・統計・研究",
    lead="一番大きい数字と、その裏の数字（満足と不満、増えたと減った）。誰が・いつ・何人に聞いたかを2文目に。",
    flow="一番のニュースになる数字 → 裏の数字・反対の数字 → 調べた対象・人数・時期・方法 → 前回との比較 → 調べた側の説明・限界 → 次の調査・発表の予定",
    must=(
        MustHave("headline", "一番大きい数字", "割合・件数・額",
                 _re(PCT + r"|\d[\d,]*\s?(?:件|人|社|倍)"), _re(PCT + r"|\d[\d,]*\s?(?:件|人|社|倍)"), True),
        MustHave("size", "調べた対象・人数・時期", "回答数・期間・方法",
                 _re(r"回答|有効回答|n\s?=\s?\d|サンプル|調査期間|調査時期|調査対象|participants|respondents|surveyed"),
                 _re(r"回答|調査期間|対象|期間|人|社"), True),
        MustHave("compare", "前回・前年との比較", "前回の数字があれば",
                 _re(r"前回|前年|前月|昨年|previous|last year|compared"), None),
    ),
    avoid="判定カード・料金・「もう使える？」の章は作らない。良い数字だけを選ばない（裏の数字も書く）。",
    length=(1600, 2400),
    examples=("満足は71.7%、一方で31.2%はほとんど使っていない",),
))

_add(NewsType(
    key="people",
    label="人事・組織",
    lead="誰が・どの役職に・いつ就く（退く）か。組織の変更なら何がどう変わるか。",
    flow="人事・組織の中身 → 時期 → 前任者・これまでの経歴（資料にあるもの）→ 発表者が説明した理由 → 事業・利用者への影響（資料にあるときだけ）",
    must=(
        MustHave("who", "人・役職・時期", "氏名・役職・就任日",
                 _re(DATE + r"|就任|退任|任命|appoint|named|effective"), _re(r"就任|退任|任命|\d{1,2}月|付")),
    ),
    avoid="判定カード・料金・ライバル比較の章は作らない。人柄の想像や評価は書かない。",
    length=(1600, 2400),
))

_add(NewsType(
    key="event",
    label="イベント・発表会のまとめ",
    lead="いつ・どこで（どのイベントで）・主な発表が何だったか（2〜3個の名前）。日本向けの発表があれば2文目に。",
    flow="主な発表を大事な順に（1つにつき名前・何ができる・いつ・価格を2〜3文）→ 日本向けの発表 → 次のイベント・提供の予定",
    must=(
        MustHave("items", "主な発表の名前と中身", "発表ごとに名前と1〜2文",
                 None, None),
    ),
    avoid="発表ごとに「料金は？」「日本で使える？」を並べない。告知だけ（日程・参加方法だけ）の記事にしない。",
    length=(1600, 3000),
))

_add(NewsType(
    key="breaking",
    label="速報・災害・事故",
    lead="何が・いつ・どこで起きたか、公式の発表元。交通・サービスへの影響が分かれば2文目に。",
    flow="起きたこと（公式の発表どおり）→ 影響（交通・サービス・対象地域）→ 公式の今後の発表の予定・問い合わせ先",
    must=(
        MustHave("when", "いつ・どこで", "公式の発表の時刻と場所",
                 _re(DATE + r"|\d{1,2}時|午前|午後"), _re(r"\d{1,2}月|\d{1,2}時|午前|午後"), True),
    ),
    avoid="推測・被害の想像・原因の決めつけは書かない。公式の発表に無い数字は書かない。",
    length=(1600, 2400),
))

_add(NewsType(
    key="sports",
    label="スポーツの結果・発表",
    lead="結果（スコア・順位・記録）か発表の中身と、日時・大会名。",
    flow="結果・発表の中身 → 日時・会場・大会 → 記録・節目 → 次の試合・日程",
    must=(
        MustHave("result", "結果・日程", "スコア・順位・日時",
                 _re(r"\d+\s?[-－対]\s?\d+|位|勝|敗|優勝|" + DATE), _re(r"\d+\s?[-－対]\s?\d+|位|勝|敗|優勝|\d{1,2}月"), True),
    ),
    avoid="判定カード・料金の章は作らない。選手の私生活・うわさは書かない。",
    length=(1600, 2400),
))

_add(NewsType(
    key="explainer",
    label="解説・技術ブログ・事例",
    lead="この記事で分かる一番大事なこと（結論）を、数字か固有名詞と一緒に。",
    flow="結論 → 仕組み・手順（資料にあるもの）→ 数字で示された効果（資料の数字だけ）→ 使える条件・注意点",
    must=(
        MustHave("point", "結論と根拠の数字", "資料にある効果・規模の数字",
                 _re(PCT + r"|\d[\d,]*\s?(?:倍|件|人|社|時間|分)"), None),
    ),
    avoid="宣伝の言葉を地の文で書かない。資料に無い手順・画面の名前を作らない。",
    length=(1600, 2600),
))

_add(NewsType(
    key="general",
    label="その他の発表",
    lead="誰が・何を・いつ（今回の新しい事実）と、それで何が変わるか。",
    flow="今回の新しい事実 → 条件・数字 → 関係する人への影響（資料にあるときだけ）→ 次の予定",
    must=(),
    avoid="材料の無い章は作らない。",
    length=(1600, 2400),
))


# --------------------------------------------------------------------------- #
# 判定（規則だけ。LLM を使わない）
# --------------------------------------------------------------------------- #

# (種類, 題名で当たったときの点, 本文の冒頭で当たったときの点, 正規表現)
RULES: list[tuple[str, int, int, re.Pattern]] = [
    ("breaking", 6, 0, _re(r"(?:地震|津波|噴火|台風|大雨|豪雨|洪水|土砂災害|大規模停電|爆発|脱線|墜落|火災)(?:[^－—-]{0,12})(?:発生|警報|注意報|速報|観測|避難|運転見合わせ|被害|欠航|休校)|緊急地震速報|避難指示")),
    ("filing", 6, 2, _re(r"S-1(?:/A)?|F-1|10-K|10-Q|8-K|有価証券届出書|有価証券報告書|上場申請|IPO|新規上場|目論見書")),
    ("security", 5, 2, _re(r"CVE-\d{4}-\d+")),
    ("security", 4, 1, _re(r"脆弱性|ぜい弱性|vulnerabilit|security advisory|セキュリティ(?:更新|アップデート|情報)|注意喚起|ゼロデイ|zero-day")),
    ("security", 4, 1, _re(r"情報漏え?洩?い|漏洩|不正アクセス|サイバー攻撃|ランサム|障害が発生|システム障害|通信障害|outage|リコール|recall|不具合|欠陥")),
    ("security", 3, 0, _re(r"攻撃フレームワーク|マルウェア|フィッシング|詐欺の手口|threat (?:actor|intelligence|report)|exploit")),
    ("earnings", 5, 1, _re(r"決算|業績|(?:四半期|半期|通期)(?:の)?(?:決算|業績|連結)|(?:quarter|fiscal|annual)(?:ly)? results|earnings|reports? (?:first|second|third|fourth)[- ]quarter|売上高|増収|減収|増益|減益|営業利益|純利益")),
    ("pricing", 4, 1, _re(r"料金|価格改定|値上げ|値下げ|月額|年額|プラン|pricing|price (?:change|increase|cut)|subscription|利用枠|usage limits?|上限")),
    ("pricing", 2, 0, _re(r"plan\b|plans\b|tier\b|" + MONEY)),
    ("deal", 5, 1, _re(r"買収|合併|経営統合|出資|資本提携|業務提携|資本業務提携|提携|協業|連携協定|子会社化|acqui|merger|partnership|invests? in|stake|資金調達|調達")),
    ("people", 5, 0, _re(r"人事|異動|代表取締役|就任|退任|社長交代|CEO(?:に|が)?(?:就任|退任|交代)|任命|役員|新体制|組織変更|持株会社|分社|appoint|steps down|names? .* (?:CEO|CFO|president)")),
    ("policy", 4, 1, _re(r"制度|法改正|改正法|規制|法案|閣議|政府|省令|告示|補助金|助成金|支援金|給付|税制|減税|増税|控除|申請受付|大臣|知事|自治体|regulat|legislation|ガイドライン")),
    ("policy", 2, 0, _re(r"財務省|総務省|経済産業省|経産省|厚生労働省|厚労省|国土交通省|国交省|金融庁|デジタル庁|内閣府|文部科学省|環境省|東京都|日本銀行|日銀|ECB|ＥＣＢ|FRB|欧州委員会|総裁|認可")),
    ("policy", 4, 0, _re(r"利上げ|利下げ|政策金利|金融政策|政策理事会|金融政策決定会合")),
    ("research", 4, 1, _re(r"調査|統計|アンケート|白書|レポート|報告書|指数|survey|report|study|研究(?:チーム|成果|結果)|論文|分析|倒産|失業率|実態|動向|ランキング|出荷量|販売実績|生産実績|販売・生産|短観|物価|鉱工業生産|雇用")),
    ("event", 4, 0, _re(r"まとめ|今月の|this month|週刊|weekly|recap|roundup|カンファレンス|基調講演|keynote|発表会|イベントで|で発表された|新発表|開催レポート|announced in|what's new in|what’s new in")),
    ("sports", 4, 0, _re(r"試合|優勝|決勝|日本代表|代表戦|リーグ|W杯|ワールドカップ|五輪|オリンピック|選手権|レース|グランプリ|対戦|勝利|敗れ")),
    ("product", 3, 1, _re(r"発売|新製品|新型|一部改良|新規設定|新設定|オプション|新サービス|提供開始|販売開始|受注開始|新機能|新モデル|アップデート|公開|発表|登場|導入|開始|launch|introduc|unveil|now available|available (?:now|today|in)|release|rolls? out|announc|debut|preview|一般提供|GA\b|generally available|available\b|追加|対応|可能に|拡大|強化|刷新|搭載|新登場|adds?\b|new\b|now\b|you can now|update")),
    ("explainer", 3, 0, _re(r"とは[？?]?|仕組み|使い方|教訓|lessons|how (?:we|to)|解説|入門|ガイド|best practices|Part \d|事例|活用法|ポイント|なぜ|理由|インタビュー")),
    ("explainer", 3, 0, _re(r"^(?:\[video\]\s*)?(?:how|build(?:ing)?|managing|accelerat\w*|run(?:ning)?|route|keep|deploy(?:ing)?|using|getting started|inside|the rise|tackling|making|catching|wealth|distributed|online|preparing|things|the tl;dr)\b")),
]
# 同じ点のときの優先（具体的な種類を先に）
PRIORITY = ["breaking", "filing", "security", "earnings", "pricing", "deal", "people", "policy", "research", "event", "sports",
            "product", "explainer", "general"]
SPECIFIC = {"security", "earnings", "pricing", "deal", "people", "filing", "breaking", "event"}
GENRE_HINT = {"sports": "sports", "public": "policy"}
# 速報の話題で、規則が「その他」にしか当たらないとき
SCORE_MIN = 2
# 題名の「解説」型でも、発表の言葉（発売・提供開始など）が強ければ発表を先にする（「〇〇とは？」は SEO の題名に付くことがある）


def _hits(pattern: re.Pattern, text: str, cap: int = 2) -> int:
    return min(cap, len(pattern.findall(text or "")))


def classify(title: str, text: str = "", *, url: str = "", genre: str = "", bundle=None, item: dict | None = None) -> str:
    """ニュースの種類の key を1つ返す（TYPES のどれか）"""
    return classify_scores(title, text, url=url, genre=genre, bundle=bundle, item=item)[0]


def classify_scores(title: str, text: str = "", *, url: str = "", genre: str = "", bundle=None,
                    item: dict | None = None) -> tuple[str, dict[str, int]]:
    """(種類, 種類ごとの点)。題名の当たりを重く、元の資料の冒頭（1,500字）の当たりを軽く数える"""
    item = item or {}
    title = title or ""
    head = (text or "")[:1500]
    scores: dict[str, int] = {}
    in_title: set[str] = set()  # 題名で当たった種類（同じ点なら、本文だけで当たった種類より先にする）
    for key, w_title, w_text, pattern in RULES:
        hit_title = bool(pattern.search(title))
        s = (w_title if hit_title else 0) + w_text * _hits(pattern, head)
        if hit_title:
            in_title.add(key)
        if s:
            scores[key] = scores.get(key, 0) + s
    low_url = (url or item.get("url") or "").lower()
    if "sec.gov" in low_url:
        scores["filing"] = scores.get("filing", 0) + 6
    if re.search(r"jpcert\.or\.jp/at/|jvn\.jp|nvd\.nist\.gov|/security/advisor|cve\.org|security-advisor", low_url):
        scores["security"] = scores.get("security", 0) + 6
    if re.search(r"/investor|/ir/|/financial|earnings|quarterly-results", low_url):
        scores["earnings"] = scores.get("earnings", 0) + 3
    if item.get("quake"):
        scores["breaking"] = scores.get("breaking", 0) + 10
    if bundle is not None:
        if getattr(bundle, "ladder", None):
            scores["pricing"] = scores.get("pricing", 0) + 3
        if getattr(bundle, "money", False):
            scores["policy"] = scores.get("policy", 0) + 2
    if genre in GENRE_HINT:
        scores[GENRE_HINT[genre]] = scores.get(GENRE_HINT[genre], 0) + 3
    # 「料金」の言葉だけの製品発表（「価格は〇〇円」）は製品。題名に料金・プランの言葉があるときだけ料金の記事
    if "pricing" in scores and not re.search(r"料金|値上げ|値下げ|価格改定|月額|年額|プラン|plan|pricing|subscription|利用枠|usage limit|上限", title, re.I):
        scores["pricing"] = min(scores["pricing"], 1)
    # 発表の言葉が題名にあり、解説の言葉が「とは」だけ（SEO の題名）なら発表を先に
    if scores.get("explainer") and scores.get("product", 0) >= 3 and re.fullmatch(r".*とは[？?]?.*", title) and not re.search(
            r"仕組み|使い方|教訓|lessons|how (?:we|to)|解説|入門|事例|活用法", title, re.I):
        scores["explainer"] -= 3
    if not scores or max(scores.values()) < SCORE_MIN:
        return "general", scores
    if "sec.gov" in low_url:
        in_title.add("filing")
    # 具体的な種類は、題名で当たるか本文で強く当たる（3点以上）ときだけ。本文に「上限」「脆弱性」が1〜2回出るだけの発表は発表のまま
    for key in SPECIFIC:
        if key in scores and key not in in_title and scores[key] < 3:
            scores[key] = 1
    if not scores or max(scores.values()) < SCORE_MIN:
        return "general", scores
    ranked = sorted(scores, key=lambda k: (-scores[k], k not in in_title, PRIORITY.index(k) if k in PRIORITY else 99))
    return ranked[0], scores


def get(key: str | None) -> NewsType:
    return TYPES.get(key or "", TYPES["general"])


# --------------------------------------------------------------------------- #
# 生成に渡す「型」
# --------------------------------------------------------------------------- #

# 判定カードを出さない種類で、日本の読者に関係する章の見出しの例（公開前の門 indexnow の no-japan-value に合う言葉を自然に含む）
JAPAN_HEADING_EXAMPLES = {
    "earnings": "日本の売上は1%減",
    "security": "日本の利用者は更新が必要",
    "deal": "日本の利用者への影響と提供の予定",
    "filing": "日本からは買えない新株",
    "research": "日本の企業の6割が使い方に悩む",
    "people": "日本法人の体制は変わらない",
    "event": "日本で使える発表は2つ",
    "breaking": "日本の利用者への影響",
    "explainer": "使い方は3つの手順",
    "general": "日本の利用者への影響",
}

HEADINGS_RULE = """- 見出し（## と ###）は、その記事でしか使えない中身の言葉にする（例「{ex}」）。質問の形の決まり文句
  （「料金は？」「日本で使える？」「自分に関係ある？」「ライバルと比べてどう？」「もう使える？」「何がすごいの？」「数字で見ると？」）、
  「概要」「詳細」「ポイント」「まとめ」だけの見出しは使わない。
- 見出しの数は、本文1,000字につき1つまで（本文が2,000字未満なら見出しは2個まで、3,000字未満なら3個まで。
  「## まだ分からないこと」は数えない）。`###` は使わない（表や手順の区切りは太字の1行で）。超えた見出しはプログラムが太字の行に直す。
- 日本の読者に関係する点（日本での提供・日本の数字・料金・使い方・始め方）を扱う章があれば、その見出しに
  「日本」「料金」「使い方」「始め方」「無料」「使える」のどれかを自然に入れる（例「日本の売上は1%減」「日本で使えるのは12月から」「料金は月200ドルのまま」）。
- 材料の無い章は作らない。「元記事」「言及なし」「記載なし」「書かれていません」「確認できていません」は本文に書かない。
  読者にとって大事なのに発表されていないことは、本文の最後に「## まだ分からないこと」として箇条書きで1回だけ書く
  （「〇〇はまだ発表されていません」のように、何が未定かを主語にする）。無ければこの章も作らない。"""


def prompt_block(key: str, *, genre_slug: str = "", source_kind: str = "web") -> str:
    """章立ての代わりに生成へ渡す、種類ごとの型（body_markdown の書き方）"""
    t = get(key)
    lo, hi = t.length
    must_lines = "\n".join(f"  - {m.label}（{m.hint}）" for m in t.must) or "  - 今回の新しい事実（誰が・何を・いつ）"
    japan_rule = "" if t.verdict else (
        "\n- この種類の記事には「使えるか」の判定カードを出さない。そのかわり、見出しの少なくとも1つは日本の読者に関係する章にし、"
        "「日本」「使い方」「料金」「無料」「始め方」「使える」のどれかを入れる（必須。例「" + JAPAN_HEADING_EXAMPLES.get(t.key, "日本の利用者への影響") + "」）。")
    ex = "」「".join(t.examples) if t.examples else "iPhoneは17%増、日本は1%減"
    src = {"web": "元の資料", "video": "動画", "trend": "関連報道", "roundup": "元の記事"}.get(source_kind, "元の資料")
    return f"""**この記事の種類: {t.label}**（決まった章立ては使わない。下の型で、材料のあることだけを書く）

リード（lede）と本文の最初の段落:
- 1文目で「今回なにが新しいか」を言い切る（誰が・何を・いつ）。2文目に、読者にとって一番大事な数字か条件。
- この種類で最初の2文に入れること: {t.lead}
- 発表した人ではなく、起きたこと・変わったことを主語にする（×「〇〇は…について発表しました」だけで終わる1文）。

{src}にあれば必ず入れる事実（無い項目は書かない。推測で埋めない）:
{must_lines}

書く順番の目安（見出しの名前ではない。中身の順番）:
{t.flow}

書かないこと: {t.avoid}

長さの目安: 本文 {lo:,}〜{hi:,}字（必ず1,600字以上。本文が1,500字に届かない記事は公開しない決まり）。足りないときは、{src}と「確認済みの事実の束」にある事実
（前の版・前回との違い、これまでの経緯、数字の内訳、条件の細部）で補う。つなぎの文・推測・一般論で埋めない。リード・要点・本文・まとめで同じ文や同じ数字をくり返さない。
「## 概要」「## これまでの流れ」の章は作らない（リードが概要の役目。過去の発表に触れるときは本文の中で1文まで、資料の日付どおり）。本文はリードの続き（リードに無い新しい事実）から始める。
最後を「まとめ」にするなら、要約のくり返しではなく、次に起きること（日付つき）か読者がやること（具体的な手順・窓口）を書く。

{HEADINGS_RULE.format(ex=ex)}{japan_rule}"""


EASY_RULE = """- easy_markdown: 「かんたんに読む」版。必ず「## ひとことで言うと」（1〜2文の要約）から始める。そのあとは中身の言葉の見出しを0〜2個
  （「何がすごいの？」「自分に関係ある？」「使ってみるには」のような決まった質問の見出しは使わない）。
  専門用語は使わないか、初めて出たところで（ ）で短く言い換える。大人の読者向けの落ち着いたです・ます調
  （幼い言い方・「すごい」「〜しちゃう」・子ども向けの例えは使わない）。body_markdown に無い事実・数字・価格は書かない。"""


FIXED_HEADING_NOTE_RE = re.compile(r"##|章立て|「自分に関係ある|「もう使える|「使ってみる|「料金は|「日本で使える|言及なし|案内なし")


def clean_field_notes(notes: str) -> str:
    """ジャンルの注意書きから、決まった章立てを前提にした行（「## 自分に関係ある？」では…など）を外す"""
    return "\n".join(ln for ln in (notes or "").split("\n") if not FIXED_HEADING_NOTE_RE.search(ln))


def missing_facts(key: str, body: str, source_text: str, *, hard_only: bool = True) -> list[MustHave]:
    """資料にあるのに本文に無い「必ず入れる事実」。hard_only=False ならチェックリストの項目も"""
    out = []
    for must in get(key).must:
        if hard_only and not must.hard:
            continue
        if must.source_re is None or must.body_re is None:
            continue
        if must.source_re.search(source_text or "") and not must.body_re.search(body or ""):
            out.append(must)
    return out


def missing_feedback(key: str, missing: list[MustHave]) -> list[str]:
    """作り直しのときに渡す指摘の文"""
    t = get(key)
    return [f"{t.label}の記事に必要な「{m.label}」が資料にあるのに本文にありません（{m.hint}）。資料の表記どおりに入れてください"
            for m in missing]


def allows_verdict(key: str) -> bool:
    return get(key).verdict


# --------------------------------------------------------------------------- #
# 試し
# --------------------------------------------------------------------------- #


def _article_type(article: dict) -> tuple[str, dict]:
    title = article.get("videoTitle") or article.get("originalTitle") or article.get("title") or ""
    text = "\n".join([article.get("title") or "", article.get("lede") or "", article.get("body") or ""])
    return classify_scores(title, text, url=article.get("sourceUrl") or "", genre=article.get("genreSlug") or "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--recent", type=int, default=0)
    parser.add_argument("--show-prompt", action="store_true")
    args = parser.parse_args()
    paths = [ARTICLES_DIR / f"{s}.json" for s in args.slug]
    if args.recent:
        rows = []
        for p in ARTICLES_DIR.glob("*.json"):
            a = json.loads(p.read_text("utf-8"))
            if a.get("sourceType") == "web" and a.get("type") != "roundup":
                rows.append((a.get("generatedAt", ""), p))
        paths += [p for _, p in sorted(rows, reverse=True)[: args.recent]]
    counts: dict[str, int] = {}
    for p in paths:
        a = json.loads(p.read_text("utf-8"))
        key, scores = _article_type(a)
        counts[key] = counts.get(key, 0) + 1
        print(f"{key:10} {p.stem[:50]:50} {(a.get('videoTitle') or a.get('title') or '')[:50]}  {scores}")
        if args.show_prompt:
            print(prompt_block(key))
    if args.recent:
        print(json.dumps(counts, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
