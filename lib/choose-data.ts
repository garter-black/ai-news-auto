/**
 * 選び方ガイド（/choose/<slug>）の中身。数字はすべて各社の公式ページから（確認日は checkedAt）。
 * 提携リンクは書かない：programId（A8）と names から、提携済みなら自動で提携リンクに切り替わる（lib/choose.ts）。
 */

/** 表のセルの色分け：good=うれしい点、bad=気をつける点、なし=ふつう */
import { difyHosting } from "./choose-dify";

export type Tone = "good" | "bad";
export type Cell = { text: string; tone?: Tone };

export type ChooseService = {
  id: string;
  name: string;
  /** 運営会社 */
  maker: string;
  /** 種類（光回線・ホームルーターなど） */
  kind: string;
  /** A8 のプログラムID（提携したら自動で提携リンクになる） */
  programId?: string;
  /** affiliate_links.json / offers.json の名前に照合する正規表現（programId で見つからないとき用） */
  names?: string;
  officialUrl: string;
  cells: Record<string, Cell>;
  /** 向いている人（1文） */
  bestFor: string;
  /** 注意点 */
  cautions: string[];
  sources: { label: string; url: string }[];
};

export type ChooseGuide = {
  slug: string;
  title: string;
  h1: string;
  description: string;
  keywords: string[];
  lead: string;
  publishedAt: string;
  updatedAt: string;
  /** 料金・特典を公式サイトで確かめた日 */
  checkedAt: string;
  tableNote?: string;
  extraSources?: { label: string; url: string }[];
  /** 表の列（1列目のサービス名は別） */
  columns: { key: string; label: string }[];
  services: ChooseService[];
  /** 表を種類ごとに区切る */
  groups: { label: string; ids: string[] }[];
  /** 用途から考える（本文の前半） */
  needs: { title: string; body: string }[];
  personas: { who: string; why: string; picks: string[] }[];
  points: { title: string; body: string }[];
  faq: { q: string; a: string }[];
  /** 関連する記事を見つける正規表現（見出し・タグに照合） */
  articleMatch: string;
  /** 記事から案内するジャンル */
  articleGenres: string[];
  /** 記事内の案内の文言 */
  articleLabel: string;
  articleNote: string;
};

const CHECKED = "2026-10-29";

const internet: ChooseGuide = {
  slug: "internet-for-ai",
  title: "AIを使うための通信回線の選び方｜光回線・ホームWi-Fi・WiMAX・スマホ回線を公式情報で比較",
  h1: "AIを快適に使うための、通信回線の選び方",
  description:
    "動画生成AI、大きなファイルのやり取り、ビデオ会議、ローカルAIのモデルのダウンロード。AIの使い方ごとに必要な回線を整理し、光回線・ホームWi-Fi・WiMAX・スマホ回線10件の月額・データ量・契約期間・特典を公式サイトの情報で比べました。",
  keywords: ["光回線 比較", "ホームWi-Fi 比較", "WiMAX 比較", "AI 回線", "動画生成AI 回線", "テレワーク 回線", "縛りなし 光回線"],
  lead:
    "生成AIそのものはクラウドで動くので、普段の文章のやり取りなら回線はあまり選びません。差が出るのは、動画や大きなファイルを扱うとき、ビデオ会議をしながら画面を共有するとき、ローカルAIのモデルをダウンロードするときです。使い方から逆算して、10件を同じ物差しで比べました。",
  publishedAt: "2026-10-29T12:00:00+09:00",
  updatedAt: "2026-10-29T12:00:00+09:00",
  checkedAt: CHECKED,
  columns: [
    { key: "price", label: "月額（税込）" },
    { key: "data", label: "データ量" },
    { key: "speed", label: "最大速度（公称）" },
    { key: "contract", label: "契約期間・解約金" },
    { key: "start", label: "工事・使い始め" },
    { key: "perk", label: "特典" },
  ],
  groups: [
    { label: "光回線（自宅で速さと安定を重視）", ids: ["gmo-hikari", "biglobe-hikari", "nuro-hikari", "otegaru-hikari", "docomo-hikari"] },
    { label: "ホームルーター・WiMAX（工事なしで使う）", ids: ["gmo-homewifi", "gmo-wimax", "biglobe-wimax"] },
    { label: "スマホ回線（外出先でテザリング）", ids: ["rakuten-mobile", "ahamo"] },
  ],
  services: [
    {
      id: "gmo-hikari",
      name: "GMOとくとくBB光",
      maker: "GMOインターネットグループ",
      kind: "光回線",
      programId: "s00000000018040",
      names: "とくとくBB\\s*光|GMO\\s*光アクセス|GMOとくとくBB光",
      officialUrl: "https://gmobb.jp/service/gmohikari/",
      cells: {
        price: { text: "1ギガ：戸建て5,390円／マンション4,290円。10ギガ：5,940円" },
        data: { text: "無制限", tone: "good" },
        speed: { text: "最大1Gbps（10ギガは一部エリアで最大10Gbps）" },
        contract: { text: "契約期間の縛りなし・解約違約金0円", tone: "good" },
        start: { text: "開通工事あり。新規工事費は36か月の利用で実質0円", tone: "bad" },
        perk: { text: "公式の申込ページで5,000円キャッシュバック（10ギガは+20,000円）" },
      },
      bestFor: "スマホの会社に縛られず、契約期間なしで光回線を安く使いたい人",
      cautions: [
        "工事費は36回の分割相当を値引きする形なので、36か月より前に解約すると残りを一括で払います。",
        "別途、事務手数料3,300円がかかります。10ギガは提供エリアが限られます。",
      ],
      sources: [{ label: "GMOとくとくBB光 公式", url: "https://gmobb.jp/service/gmohikari/" }],
    },
    {
      id: "biglobe-hikari",
      name: "BIGLOBE光",
      maker: "ビッグローブ",
      kind: "光回線",
      programId: "s00000000040016",
      names: "BIGLOBE\\s*光",
      officialUrl: "https://join.biglobe.ne.jp/ftth/hikari/",
      cells: {
        price: { text: "1ギガ：戸建て5,478円／集合住宅4,378円。10ギガ：6,270円" },
        data: { text: "無制限", tone: "good" },
        speed: { text: "最大1Gbps（10ギガは一部エリアで最大10Gbps）" },
        contract: { text: "1ギガは3年、10ギガは2年の自動更新。期間内の解約は3,000〜4,620円", tone: "bad" },
        start: { text: "開通工事あり。新規工事費（最大28,600円）は特典で実質0円", tone: "bad" },
        perk: { text: "新規工事費の実質0円。auスマートバリューなどスマホとのセット割あり" },
      },
      bestFor: "auやUQ mobileのスマホを使っていて、セット割も合わせて考えたい人",
      cautions: [
        "1ギガは3年ごとの自動更新です。更新月とその翌月・翌々月以外の解約は契約解除料がかかります。",
        "工事費は分割相当を値引きする形なので、途中で解約すると残りを一括で払います。",
      ],
      sources: [
        { label: "BIGLOBE光 公式", url: "https://join.biglobe.ne.jp/ftth/hikari/" },
        { label: "料金表", url: "https://join.biglobe.ne.jp/ftth/hikari/price/" },
      ],
    },
    {
      id: "nuro-hikari",
      name: "NURO 光",
      maker: "ソニーネットワークコミュニケーションズ",
      kind: "光回線",
      programId: "s00000013431001",
      names: "NURO\\s*光",
      officialUrl: "https://www.nuro.jp/",
      cells: {
        price: { text: "戸建てなど：4,530円（2年割の24か月間・Wi-Fiルーター込み。25か月目から5,720円）" },
        data: { text: "無制限", tone: "good" },
        speed: { text: "下り最大2.5Gbps（10ギガは最大10Gbps）", tone: "good" },
        contract: { text: "36か月の自動更新。戸建てなどの契約解除料は3,643円", tone: "bad" },
        start: { text: "開通工事あり。基本工事費55,000円は36か月で実質無料。提供エリアが限られる", tone: "bad" },
        perk: { text: "他社の解約費用を最大6万円還元。2年割の申込で5,000円キャッシュバック（11/1まで）" },
      },
      bestFor: "提供エリア内に住んでいて、標準プランから速さを重視したい人",
      cautions: [
        "提供エリアが限られるため、申し込み前に住所で確認が必要です。",
        "40戸以上の集合住宅は別プラン（M）になり、料金が変わります。事務手数料6,050円が別にかかります。",
      ],
      sources: [
        { label: "NURO 光 公式", url: "https://www.nuro.jp/" },
        { label: "戸建ての料金", url: "https://www.nuro.jp/hikari/house/price/" },
      ],
    },
    {
      id: "otegaru-hikari",
      name: "おてがる光",
      maker: "エクスゲート",
      kind: "光回線",
      programId: "s00000022354001",
      names: "おてがる光",
      officialUrl: "https://otegal.jp/",
      cells: {
        price: { text: "戸建て4,708円／マンション3,608円" },
        data: { text: "無制限", tone: "good" },
        speed: { text: "最大1Gbps" },
        contract: { text: "契約期間・違約金なし", tone: "good" },
        start: { text: "開通工事あり。派遣工事費22,000円は2,000円×最大11回の割引", tone: "bad" },
        perk: { text: "新規は初月の基本料0円など" },
      },
      bestFor: "引っ越しの予定があるなど、いつでも解約できることを優先したい人",
      cautions: ["工事費の割引は最大11回なので、早く解約すると工事費が残ることがあります。", "10ギガの選択肢は公式サイトで確認してください。"],
      sources: [{ label: "おてがる光 公式", url: "https://otegal.jp/" }],
    },
    {
      id: "docomo-hikari",
      name: "ドコモ光",
      maker: "NTTドコモ",
      kind: "光回線",
      officialUrl: "https://www.docomo.ne.jp/internet/hikari/charge/index.html",
      cells: {
        price: { text: "1ギガ タイプA：戸建て5,720円／マンション4,400円（2年定期契約）" },
        data: { text: "無制限", tone: "good" },
        speed: { text: "最大1Gbps（10ギガは6,380円）" },
        contract: { text: "2年定期契約。期間内の解約は戸建て5,500円／マンション4,180円", tone: "bad" },
        start: { text: "開通工事あり（工事料の代表例28,600円、新規は実質0円の特典あり）", tone: "bad" },
        perk: { text: "ドコモのスマホとセットで、スマホ料金が1回線あたり最大1,210円/月割引" },
      },
      bestFor: "家族でドコモのスマホを使っていて、セット割の効果が大きい人",
      cautions: ["定期契約なしのプランは月額が高くなります（戸建て7,370円／マンション5,500円）。", "プロバイダの種類（タイプA・B・単独）で料金が変わります。"],
      sources: [{ label: "ドコモ光 料金プラン", url: "https://www.docomo.ne.jp/internet/hikari/charge/index.html" }],
    },
    {
      id: "gmo-homewifi",
      name: "GMOとくとくBB ホームWi-Fi",
      maker: "GMOインターネットグループ",
      kind: "ホームルーター",
      programId: "s00000000018046",
      names: "とくとくBB\\s*ホームWi-?Fi|GMOとくとくBB\\s*ホームWi-?Fi",
      officialUrl: "https://gmobb.jp/gmohomewifi/",
      cells: {
        price: { text: "5,687円（端末代33,264円は分割相当を毎月割引）" },
        data: { text: "無制限（大量に使うと混雑時に速度制限の場合あり）", tone: "good" },
        speed: { text: "下り最大4.2Gbps（5G通信時・端末の規格上の最大値）" },
        contract: { text: "解約違約金なし", tone: "good" },
        start: { text: "工事不要。コンセントに挿して使う", tone: "good" },
        perk: { text: "20,000円キャッシュバック、乗り換え時は他社違約金相当を最大50,000円など" },
      },
      bestFor: "工事ができない部屋に住んでいて、家の中だけで使えればよい人",
      cautions: ["光回線より混雑の影響を受けやすく、速度は電波の状況で大きく変わります。", "事務手数料3,300円が別にかかります。"],
      sources: [{ label: "GMOとくとくBB ホームWi-Fi 公式", url: "https://gmobb.jp/gmohomewifi/" }],
    },
    {
      id: "gmo-wimax",
      name: "GMOとくとくBB WiMAX",
      maker: "GMOインターネットグループ",
      kind: "WiMAX（持ち運び・据え置き）",
      programId: "s00000000018021",
      names: "とくとくBB.*WiMAX|GMOとくとくBB\\s*WiMAX",
      officialUrl: "https://gmobb.jp/wimax/",
      cells: {
        price: { text: "端末発送月1,375円、翌月から5,566円（端末代は分割相当を割引）" },
        data: { text: "無制限（大量に使うと混雑時に速度制限の場合あり）", tone: "good" },
        speed: { text: "下り最大3.5Gbps（持ち運び型）／4.2Gbps（据え置き型）" },
        contract: { text: "解約違約金なし", tone: "good" },
        start: { text: "工事不要。最短即日発送", tone: "good" },
        perk: { text: "他社回線からの乗り換えで最大40,000円キャッシュバック" },
      },
      bestFor: "家でも外でも同じ1台で、ノートPCからAIを使いたい人",
      cautions: ["混雑する時間帯は速度が落ちることがあります。", "プラスエリアモードを使った月は1,100円が追加でかかります。"],
      sources: [{ label: "GMOとくとくBB WiMAX 公式", url: "https://gmobb.jp/wimax/" }],
    },
    {
      id: "biglobe-wimax",
      name: "BIGLOBE WiMAX +5G",
      maker: "ビッグローブ",
      kind: "WiMAX（持ち運び・据え置き）",
      programId: "s00000000040014",
      names: "BIGLOBE\\s*WiMAX",
      officialUrl: "https://join.biglobe.ne.jp/mobile/wimax/5g/",
      cells: {
        price: { text: "5,258円（24か月間は1,650円引き）＋端末924円×36回" },
        data: { text: "無制限（大量に使うと混雑時に速度制限の場合あり）", tone: "good" },
        speed: { text: "下り最大3.5Gbps（持ち運び型）／4.2Gbps（据え置き型）" },
        contract: { text: "最低利用期間なし・契約解除料なし", tone: "good" },
        start: { text: "工事不要。最短翌日発送", tone: "good" },
        perk: { text: "端末の同時申込＋クーポン利用で、翌月11,500円キャッシュバック" },
      },
      bestFor: "auやUQ mobileのスマホを使っていて、工事なしの回線を探している人",
      cautions: ["端末代を分割の途中で解約すると、残りを払う必要があります。", "申込手数料3,300円が別にかかります。"],
      sources: [{ label: "BIGLOBE WiMAX +5G 公式", url: "https://join.biglobe.ne.jp/mobile/wimax/5g/" }],
    },
    {
      id: "rakuten-mobile",
      name: "楽天モバイル",
      maker: "楽天モバイル",
      kind: "スマホ回線",
      programId: "s00000027494001",
      names: "楽天モバイル|Rakuten最強プラン",
      officialUrl: "https://network.mobile.rakuten.co.jp/fee/saikyo-plan/",
      cells: {
        price: { text: "3GBまで1,078円／20GBまで2,178円／無制限3,278円（使った分だけ）" },
        data: { text: "無制限（混雑時などは速度制御の場合あり）", tone: "good" },
        speed: { text: "通信速度の数値は公表されていません" },
        contract: { text: "1年以内の解約は解約事務手数料（最大1,078円）。1年経過後は0円" },
        start: { text: "工事不要（SIM・eSIM）", tone: "good" },
        perk: { text: "最強家族割で各段階110円引き。キャンペーンは公式で確認" },
      },
      bestFor: "外出先でスマホからAIを使うことが多く、使った分だけ払いたい人",
      cautions: ["電波の届き方は場所によって差があるため、通信エリアを確認してください。", "解約事務手数料は2025年4月以降の新規回線の条件です。"],
      sources: [
        { label: "Rakuten最強プラン", url: "https://network.mobile.rakuten.co.jp/fee/saikyo-plan/" },
        { label: "解約事務手数料のお知らせ", url: "https://network.mobile.rakuten.co.jp/information/news/service/3306/" },
      ],
    },
    {
      id: "ahamo",
      name: "ahamo",
      maker: "NTTドコモ",
      kind: "スマホ回線",
      officialUrl: "https://ahamo.com/",
      cells: {
        price: { text: "30GB 2,970円／大盛り110GB 4,950円（12/1から40GB 3,135円・60GB 4,125円・120GB 5,115円に改定）" },
        data: { text: "30GB（大盛りで110GB）。テザリングOK" },
        speed: { text: "公式ページで確認" },
        contract: { text: "公式ページで確認" },
        start: { text: "工事不要（SIM・eSIM）", tone: "good" },
        perk: { text: "5分以内の国内通話かけ放題つき" },
      },
      bestFor: "ドコモの回線で、毎月のデータ量がだいたい決まっている人",
      cautions: ["2026年12月1日から料金が段階制に変わり、大盛りオプションは11月30日で終了します。"],
      sources: [{ label: "ahamo 公式", url: "https://ahamo.com/" }],
    },
  ],
  needs: [
    {
      title: "チャットAIで文章をやり取りするだけ",
      body: "送る文字の量はわずかなので、どの回線でも困ることはほとんどありません。スマホ回線のテザリングでも十分です。",
    },
    {
      title: "動画生成AI・画像生成AIをよく使う",
      body: "生成した動画をダウンロードしたり、素材をアップロードしたりする量が増えます。データ量に上限がなく、上り（アップロード）も速い光回線が向いています。",
    },
    {
      title: "ビデオ会議でAIの画面を共有する",
      body: "速さより、途切れにくさが大事です。家で長時間つなぐなら光回線、工事できない場合はホームルーターを窓の近くに置くのが現実的です。",
    },
    {
      title: "ローカルAI（手元のPCで動かすAI）を試す",
      body: "モデルのファイルは数GB〜数十GBになることがあります。何度も入れ替えるなら、データ量が無制限で速い光回線が安心です。",
    },
    {
      title: "外出先や移動中にノートPCでAIを使う",
      body: "持ち運べるWiMAXか、スマホ回線のテザリングが候補です。月のデータ量が読めないなら、無制限のプランを選ぶと計算がいりません。",
    },
  ],
  personas: [
    { who: "家で動画生成AIやローカルAIをよく使う", why: "データ量の上限がなく、上りも速い光回線が向いています。縛りなしならGMOとくとくBB光、速さ重視ならNURO 光（エリア内のみ）。", picks: ["gmo-hikari", "nuro-hikari"] },
    { who: "工事ができない・引っ越しが多い", why: "工事不要で、解約金のないホームルーターかWiMAXが手軽です。家の中だけならホームWi-Fi、外にも持ち出すならWiMAX。", picks: ["gmo-homewifi", "biglobe-wimax"] },
    { who: "外出先でノートPCからAIを使う", why: "持ち運べるWiMAXか、スマホ回線のテザリングで。使った分だけ払う楽天モバイルか、量が決まっているならahamo。", picks: ["gmo-wimax", "rakuten-mobile", "ahamo"] },
    { who: "スマホ代とまとめて安くしたい", why: "スマホと同じ会社の回線にすると、スマホ料金のセット割が効くことがあります。ドコモならドコモ光、au・UQ mobileならBIGLOBEの回線。", picks: ["docomo-hikari", "biglobe-hikari"] },
  ],
  points: [
    { title: "まず「家で使うか、外で使うか」を決める", body: "家だけなら光回線かホームルーター、外でも使うならWiMAXかスマホ回線です。両方を1つで済ませると、どちらかが中途半端になりがちです。" },
    { title: "最大速度は「規格上の数字」と考える", body: "表の速度はどれも公称値で、実際の速さは時間帯や建物、電波の状況で大きく変わります。比べるなら同じ種類の回線どうしで比べてください。" },
    { title: "月額は「何か月目の金額か」を見る", body: "割引が24か月だけのものや、端末代を分割で値引きしているものがあります。3年使ったときの合計で比べると、差がわかりやすくなります。" },
    { title: "契約期間と解約金を確かめる", body: "引っ越しの予定があるなら、縛りのない回線が安心です。工事費や端末代の残りは、解約金とは別にかかることがあります。" },
    { title: "特典は条件と時期を確かめる", body: "キャッシュバックは申し込む窓口や時期で変わり、受け取りに手続きが必要なことがほとんどです。表の特典は10月29日に公式サイトで確認したものです。" },
  ],
  faq: [
    { q: "AIを使うのに光回線は必要？", a: "チャットで文章をやり取りするだけなら必須ではありません。動画生成AIや大きなファイル、ローカルAIのモデルのダウンロードが多いなら、データ量に上限のない光回線が安心です。" },
    { q: "ホームルーターとWiMAXの違いは？", a: "どちらも工事なしで使えます。ホームルーターは家のコンセントに挿して使う据え置き型、WiMAXには持ち運べる型と据え置き型があります。この表のGMOとくとくBB ホームWi-FiもWiMAXの回線を使う据え置き型です。" },
    { q: "表の最大速度どおりに出る？", a: "出ません。表の速度は規格上の最大値（公称）で、実際の速さは時間帯・建物・電波の状況で変わります。" },
    { q: "キャッシュバックは誰でももらえる？", a: "条件があります。申し込む窓口、オプションの加入、受け取りの手続きなどが決まっているので、各社の公式サイトで条件を確認してください。" },
    { q: "この表の情報はいつのもの？", a: "2026年10月29日に各社の公式サイトで確認した内容です。料金やキャンペーンは変わることがあるので、申し込む前に公式サイトで最新の情報を確認してください。" },
  ],
  articleMatch:
    "光回線|ホームルーター|ポケット(型)?Wi-?Fi|Wi-?Fi\\s?[67]|WiMAX|テザリング|格安SIM|楽天モバイル|ahamo|povo|LINEMO|料金プラン|通信速度|回線速度|通信障害|Starlink|eSIM|ギガ無制限|データ容量|Fast Access|スマホ料金|携帯料金|通信料金|5G",
  articleGenres: ["ai", "tech-gadget"],
  articleLabel: "AIを快適に使うための、通信回線の選び方",
  articleNote: "光回線・ホームWi-Fi・WiMAX・スマホ回線10件を公式情報で比較",
};

const career: ChooseGuide = {
  slug: "ai-career",
  title: "AIスキルを生かす転職・学び直しの選び方｜IT転職エージェントとAI講座・公的支援を比較",
  h1: "AIスキルを生かす転職・学び直しサービスの選び方",
  description:
    "AIの仕事に就きたい、AIを学び直したい人向けに、IT転職エージェント4社、AI講座、国の支援制度を、対象になる人（経験・地域）・費用・できることで比べました。条件は各公式サイトで2026年10月29日に確認しています。",
  keywords: ["AI 転職", "エンジニア 転職エージェント", "リスキリング AI", "AI 学び直し", "教育訓練給付金", "ゲーム業界 転職"],
  lead:
    "AIで仕事の中身が変わるなか、「AIを使える人」として転職するか、今の職場でAIを学び直すか。選び方は、今の経験と住んでいる地域でかなり絞れます。転職エージェントは無料で使えますが、対象にしている人がそれぞれ違うため、合わないところに登録しても紹介を受けにくいのが実情です。",
  publishedAt: "2026-10-29T12:00:00+09:00",
  updatedAt: "2026-10-29T12:00:00+09:00",
  checkedAt: CHECKED,
  columns: [
    { key: "target", label: "向いている人" },
    { key: "area", label: "地域" },
    { key: "cost", label: "利用者の費用" },
    { key: "what", label: "できること" },
    { key: "note", label: "気をつける点" },
  ],
  groups: [
    { label: "IT・ゲーム業界の転職エージェント", ids: ["levtech", "geekly", "techgo", "gjob"] },
    { label: "AIを学び直す", ids: ["ai-connect", "kyouiku-kyufu", "reskilling-meti"] },
  ],
  services: [
    {
      id: "levtech",
      name: "レバテックキャリア",
      maker: "レバレジーズ",
      kind: "IT・Web転職エージェント",
      programId: "s00000011866006",
      names: "レバテック",
      officialUrl: "https://career.levtech.jp/",
      cells: {
        target: { text: "ITエンジニア・デザイナーの実務経験がある人" },
        area: { text: "東京・大阪・福岡などの求人" },
        cost: { text: "無料（登録・相談）", tone: "good" },
        what: { text: "IT・Webに絞った求人53,000件以上（2026年1月時点・非公開求人を含む）。電話・オンライン相談" },
        note: { text: "掲載の事例は実務経験のある人", tone: "bad" },
      },
      bestFor: "エンジニアの経験があり、AIを扱う開発の仕事に移りたい人",
      cautions: ["紹介される求人は経験者向けが中心です。未経験なら先に学び直しを考えましょう。"],
      sources: [{ label: "レバテックキャリア 公式", url: "https://career.levtech.jp/" }],
    },
    {
      id: "geekly",
      name: "Geekly（ギークリー）",
      maker: "ギークリー",
      kind: "IT・Web・ゲーム転職エージェント",
      programId: "s00000016183001",
      names: "Geekly|ギークリー",
      officialUrl: "https://www.geekly.co.jp/",
      cells: {
        target: { text: "IT・Web・ゲーム業界で働きたい人。職種や業界が未経験の人の支援実績もあると記載" },
        area: { text: "関東・関西・東海など全国8地域の求人を掲載" },
        cost: { text: "無料（登録・相談）", tone: "good" },
        what: { text: "求人44,000件以上（2026年9月時点）。対面またはオンラインでヒアリング" },
        note: { text: "希望する地域の求人数を検索で確認" },
      },
      bestFor: "エンジニア以外の職種（企画・マーケなど）も含めて、IT業界を広く見たい人",
      cautions: ["掲載の求人は地域ごとに件数が違います。地方で探す人は、希望する地域で検索してから登録しましょう。"],
      sources: [{ label: "Geekly 公式", url: "https://www.geekly.co.jp/" }],
    },
    {
      id: "techgo",
      name: "TechGo（テックゴー）",
      maker: "MyVision",
      kind: "ITエンジニア転職エージェント",
      programId: "s00000024757003",
      names: "TechGo|テックゴー",
      officialUrl: "https://tech-go.jp/",
      cells: {
        target: { text: "年収を上げたいITエンジニア（年収500万〜1,000万円以上の求人を検索できる）" },
        area: { text: "東京・神奈川・愛知・大阪・福岡などの求人" },
        cost: { text: "無料（登録・相談）", tone: "good" },
        what: { text: "求人10,000件以上（2026年8月時点・公開と非公開の合計）" },
        note: { text: "対象の経験年数は公表されていません" },
      },
      bestFor: "経験を積んだエンジニアで、AI関連の上位職や年収アップを狙う人",
      cautions: ["年収条件の高い求人を探しやすい作りです。自分の経験で紹介を受けられるかは、無料相談で確かめましょう。"],
      sources: [{ label: "TechGo 公式", url: "https://tech-go.jp/" }],
    },
    {
      id: "gjob",
      name: "G-JOBエージェント",
      maker: "リンクトブレイン",
      kind: "ゲーム業界転職エージェント",
      programId: "s00000024560001",
      names: "G-JOB",
      officialUrl: "https://game-matching.jp/",
      cells: {
        target: { text: "ゲーム業界で働きたい人（プランナー・デザイナー・エンジニアなど）" },
        area: { text: "関東・関西などの求人" },
        cost: { text: "無料（相談）", tone: "good" },
        what: { text: "正社員・契約社員・派遣・フリーランスまで、働き方ごとに紹介。未経験OKの求人もある" },
        note: { text: "ゲーム業界に絞られる" },
      },
      bestFor: "生成AIで変わりつつあるゲーム開発の現場に入りたい人",
      cautions: ["ゲーム業界に特化しているため、ほかの業界も見たいならIT全般のエージェントと併用しましょう。"],
      sources: [{ label: "G-JOBエージェント 公式", url: "https://game-matching.jp/" }],
    },
    {
      id: "ai-connect",
      name: "AI CONNECT",
      maker: "D4cアカデミー",
      kind: "AI講座＋転職支援",
      programId: "s00000025233001",
      names: "AI\\s*CONNECT",
      officialUrl: "https://ai-c.net/",
      cells: {
        target: { text: "会社に勤めながらAIを学びたい人（国の事業の対象は在職者）" },
        area: { text: "オンライン（動画教材）", tone: "good" },
        cost: { text: "国の補助（最大70%）と独自キャンペーンで「実質無料」と記載。条件は無料相談で説明" },
        what: { text: "未経験から2か月でAIスキルを学ぶ講座と転職支援。30分の無料相談" },
        note: { text: "「実質無料」になる条件を必ず確認", tone: "bad" },
      },
      bestFor: "今の仕事を続けながら、AIを体系的に学び直したい会社員",
      cautions: ["経済産業省の「リスキリングを通じたキャリアアップ支援事業」の補助事業者です。補助を受ける条件は、申し込む前の無料相談で確認してください。"],
      sources: [{ label: "AI CONNECT 公式", url: "https://ai-c.net/" }],
    },
    {
      id: "kyouiku-kyufu",
      name: "教育訓練給付金",
      maker: "厚生労働省",
      kind: "国の給付制度",
      officialUrl: "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/koyou_roudou/jinzaikaihatsu/kyouiku.html",
      cells: {
        target: { text: "雇用保険の加入期間など、一定の要件を満たす人" },
        area: { text: "全国（申請はハローワーク）", tone: "good" },
        cost: { text: "指定講座の費用の一部を支給。一般20%（上限10万円）〜専門実践は最大80%", tone: "good" },
        what: { text: "厚生労働大臣が指定した講座なら、スクールを選ばず使える" },
        note: { text: "対象講座と受給要件を事前に確認" },
      },
      bestFor: "通いたい講座が決まっていて、費用の一部を取り戻したい人",
      cautions: ["受講する講座が給付の対象として指定されている必要があります。手続きはお住まいのハローワークで行います。"],
      sources: [{ label: "厚生労働省 教育訓練給付金", url: "https://www.mhlw.go.jp/stf/seisakunitsuite/bunya/koyou_roudou/jinzaikaihatsu/kyouiku.html" }],
    },
    {
      id: "reskilling-meti",
      name: "リスキリングを通じたキャリアアップ支援事業",
      maker: "経済産業省",
      kind: "国の支援事業",
      officialUrl: "https://careerup.reskilling.go.jp/",
      cells: {
        target: { text: "在職者（今の会社に勤めている人）" },
        area: { text: "採択された事業者ごとに異なる" },
        cost: { text: "講座の費用の一部を国が補助（条件は事業者ごと）", tone: "good" },
        what: { text: "キャリア相談・講座の受講・転職支援をまとめて受けられる" },
        note: { text: "転職を前提にした制度" },
      },
      bestFor: "学び直しと転職をセットで考えていて、費用を抑えたい人",
      cautions: ["講座の中身や補助の条件は、採択された事業者ごとに違います。"],
      sources: [{ label: "経済産業省 事業の案内", url: "https://careerup.reskilling.go.jp/" }],
    },
  ],
  needs: [
    { title: "エンジニアの経験があり、AIの仕事に移りたい", body: "IT専門の転職エージェントが近道です。AIを組み込む開発の求人は、経験者向けのものが中心です。" },
    { title: "エンジニア以外の職種で、AIを使う仕事に就きたい", body: "企画・マーケティング・営業など、職種を広く扱うエージェントが向いています。AIを業務でどう使ったかを話せるようにしておくと有利です。" },
    { title: "まずAIを学び直したい（会社は辞めない）", body: "国の補助がある講座や、教育訓練給付金の対象講座から選ぶと費用を抑えられます。補助の条件は講座ごとに違います。" },
    { title: "ゲーム業界で働きたい", body: "ゲーム業界専門のエージェントは、業界の職種や働き方に詳しい担当者が多いのが利点です。" },
  ],
  personas: [
    { who: "エンジニア経験があり、AI開発の仕事に移りたい", why: "IT特化で求人の多いエージェントから。年収アップを重視するならハイクラス寄りのエージェントも。", picks: ["levtech", "techgo"] },
    { who: "ゲーム業界で働きたい", why: "ゲーム専門のエージェントと、ゲームも扱うIT系エージェントを併用すると求人の幅が広がります。", picks: ["gjob", "geekly"] },
    { who: "会社員のまま、AIを学び直したい", why: "国の補助がある講座か、教育訓練給付金の対象講座を選ぶと費用を抑えられます。", picks: ["ai-connect", "kyouiku-kyufu"] },
  ],
  points: [
    { title: "対象になる人を先に確かめる", body: "転職エージェントはそれぞれ、経験・年齢・希望する地域で得意な層が違います。対象外だと求人の紹介を受けにくいので、登録前に確認しましょう。" },
    { title: "地域の求人がどれだけあるかを見る", body: "IT系の求人は首都圏に集まりがちです。地方で働きたい人は、希望する地域の求人数を検索してから登録すると無駄がありません。" },
    { title: "エージェントは2〜3社の併用が普通", body: "同じ求人でも紹介する担当者によって話が違うことがあります。比べることで、自分の市場価値も見えてきます。" },
    { title: "「実質無料」の講座は条件を読む", body: "国の補助を使う講座は、受講後の手続きや転職などが条件になることがあります。無料相談で条件を確かめてから申し込みましょう。" },
  ],
  faq: [
    { q: "未経験からAIの仕事に転職できる？", a: "AIを組み込む開発の求人は経験者向けが中心です。未経験なら、まず講座で学んでから、未経験の支援実績がある転職エージェントに相談するのが現実的です。" },
    { q: "転職エージェントにお金はかかる？", a: "この表の転職エージェントは、利用者の登録・相談は無料と公式サイトに書かれています。費用は採用した企業が払う仕組みです。" },
    { q: "AIの講座の費用を抑える方法は？", a: "厚生労働省の教育訓練給付金や、経済産業省の「リスキリングを通じたキャリアアップ支援事業」の対象講座を使うと、費用の一部が戻ったり補助されたりします。どちらも条件があります。" },
    { q: "地方に住んでいても使える？", a: "相談はオンラインでできるところが多いですが、紹介される求人は首都圏が中心のことがあります。希望する地域の求人数を先に確認してください。" },
    { q: "この表の情報はいつのもの？", a: "2026年10月29日に各公式サイトで確認した内容です。求人数や条件は変わることがあるので、申し込む前に公式サイトで最新の情報を確認してください。" },
  ],
  articleMatch:
    "転職|求人|リスキリング|学び直し|AI人材|デジタル人材|エンジニア(に|の)?(求められる|必要|像)|キャリア|就職|雇用への影響|仕事を奪|職を失|ITパスポート|G検定|資格試験",
  articleGenres: ["ai", "tech-gadget", "business"],
  articleLabel: "AIスキルを生かす転職・学び直しサービスの選び方",
  articleNote: "IT転職エージェント・AI講座・国の支援制度を、対象と費用で比較",
};

export const CHOOSE_GUIDES: ChooseGuide[] = [difyHosting, internet, career];
