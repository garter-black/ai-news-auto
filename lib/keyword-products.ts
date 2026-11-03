import type { ArticleSummary } from "@/lib/articles";

/**
 * 製品・趣味の記事（乗り物・自転車・端末・スポーツ用品・ゲームなど）で、記事に合う広告の話題が無いときの
 * 「この記事に関連する商品」。製品のカテゴリの語で、楽天市場（A8 の楽天アフィリエイト）と Amazon の検索結果へ案内する。
 * 検索語はこの表だけで決める（記事の宣伝文句・ブランドの主張は使わない）。価格は出さない。
 * 上から順に、見出し → タグの順で照合し、最初に当たったものを使う（細かいカテゴリを先に）。
 */
export type ProductCategory = {
  /** 見出しに出す短い名前 */
  name: string;
  /** 検索語（楽天・Amazon 共通） */
  query: string;
  match: RegExp;
  /** 見出しにあれば使わない（ソフト更新・決算など、製品そのものの記事でないもの） */
  exclude?: RegExp;
};

const NOT_PRODUCT = /決算|株価|倒産|脆弱性|アップデート|ソフトウェア更新|障害|リコール/;

export const PRODUCT_CATEGORIES: ProductCategory[] = [
  // 自転車・乗り物
  { name: "電動アシストマウンテンバイク", query: "電動アシスト マウンテンバイク", match: /e-?MTB|電動(アシスト)?マウンテンバイク|電動アシスト.{0,4}MTB/i },
  { name: "電動アシスト自転車", query: "電動アシスト自転車", match: /電動アシスト自転車|e-?バイク|E-?Bike/i },
  { name: "マウンテンバイク", query: "マウンテンバイク", match: /マウンテンバイク|\bMTB\b/i },
  { name: "ロードバイク", query: "ロードバイク", match: /ロードバイク|グラベルバイク/ },
  { name: "自転車用品", query: "自転車 用品", match: /自転車|サイクリング/ },
  { name: "電動キックボード", query: "電動キックボード", match: /電動キックボード|キックスクーター/ },
  { name: "バイク用品", query: "バイク用品", match: /オートバイ|二輪車|スクーター|ツーリング|バイクの新型|新型バイク/ },
  { name: "カー用品", query: "カー用品 ドライブレコーダー", match: /新型.{0,8}(SUV|セダン|ミニバン|クーペ|軽自動車|車)|SUV|ミニバン|一部改良|ドライブレコーダー|カーナビ/ },
  // 端末・パソコン
  { name: "折りたたみスマホ", query: "折りたたみスマホ", match: /折りたたみ(スマホ|スマートフォン)|Fold\s?\d|Flip\s?\d/i },
  { name: "スマートウォッチ", query: "スマートウォッチ", match: /スマートウォッチ|Apple\s?Watch|Pixel\s?Watch|Galaxy\s?Watch/i },
  { name: "ワイヤレスイヤホン", query: "ワイヤレスイヤホン", match: /イヤホン|AirPods|Buds|ヘッドホン|ヘッドフォン/i },
  { name: "タブレット", query: "タブレット", match: /タブレット|iPad|Galaxy\s?Tab/i, exclude: /iPadOS|iPad版|アプリ/ },
  { name: "スマートフォン", query: "SIMフリー スマートフォン", match: /スマホ|スマートフォン|iPhone|Galaxy\s?S\d|Pixel\s?\d|Xperia|AQUOS\s?(sense|R)/i, exclude: /iOS|料金|プラン|回線|ふるさと|研究|手法|論文|自動操作|教育|モラル|アプリ/ },
  { name: "ゲーミングPC", query: "ゲーミングPC", match: /ゲーミングPC/ },
  { name: "ミニPC", query: "ミニPC", match: /ミニPC/ },
  { name: "ノートパソコン", query: "ノートパソコン", match: /ノートPC|ノートパソコン|MacBook|Copilot\+ ?PC|Chromebook/i },
  { name: "PCモニター", query: "PCモニター", match: /モニター|ディスプレイ/, exclude: /監視|モニタリング/ },
  { name: "カメラ", query: "ミラーレス一眼", match: /ミラーレス|一眼|デジタルカメラ|デジカメ|アクションカメラ|GoPro/i },
  { name: "テレビ", query: "4Kテレビ", match: /有機ELテレビ|液晶テレビ|4Kテレビ|プロジェクター/ },
  // ゲーム
  { name: "Nintendo Switch 2のソフト", query: "Nintendo Switch 2 ソフト", match: /Switch\s?2/i },
  { name: "Nintendo Switchのソフト", query: "Nintendo Switch ソフト", match: /Nintendo\s?Switch|ニンテンドースイッチ|任天堂/i },
  { name: "PS5のソフト", query: "PS5 ソフト", match: /PS5|PlayStation\s?5|PlayStation|プレイステーション/i },
  { name: "ゲームソフト", query: "ゲームソフト", match: /DLC|体験版|ゲームソフト|新作ゲーム/ },
  // スポーツ用品（日本のスポーツの話題）
  { name: "卓球用品", query: "卓球 ラケット", match: /卓球/ },
  { name: "フットサル用品", query: "フットサルシューズ", match: /フットサル/ },
  { name: "サッカー用品", query: "サッカーボール", match: /サッカー|Jリーグ|なでしこ|日本代表.{0,6}(W杯|ワールドカップ)/ },
  { name: "野球用品", query: "野球 グローブ", match: /野球|プロ野球|甲子園|WBC|大谷/ },
  { name: "ゴルフ用品", query: "ゴルフ用品", match: /ゴルフ/ },
  { name: "テニス用品", query: "テニスラケット", match: /テニス/ },
  { name: "バスケットボール用品", query: "バスケットボール", match: /バスケ|Bリーグ/ },
  { name: "バレーボール用品", query: "バレーボール", match: /バレーボール|SVリーグ/ },
  { name: "ラグビー用品", query: "ラグビーボール", match: /ラグビー/ },
  { name: "ランニングシューズ", query: "ランニングシューズ", match: /マラソン|駅伝|ランニング|陸上/ },
  { name: "トレーニング用品", query: "トレーニング 器具", match: /筋トレ|トレーニング器具|フィットネス/ },
  // 暮らし（食品・日用品の新商品）
  { name: "コーヒー", query: "コーヒー", match: /コーヒー|ラテ|カフェオレ/ },
  { name: "スキンケア", query: "スキンケア 化粧水", match: /スキンケア|化粧水|洗顔/ },
  { name: "ビール", query: "クラフトビール", match: /ビール/ },
  { name: "お米", query: "お米 10kg", match: /新米|お米|銘柄米/ },
];

/** 記事の製品カテゴリ（見出し → タグの順）。製品そのものの記事でなければ undefined */
export function productCategoryForArticle(article: Pick<ArticleSummary, "title" | "seoTitle" | "tags">): ProductCategory | undefined {
  const title = `${article.title} ${article.seoTitle ?? ""}`;
  if (NOT_PRODUCT.test(title)) return undefined;
  const tags = (article.tags ?? []).join(" / ");
  for (const text of [title, tags]) {
    const found = PRODUCT_CATEGORIES.find((c) => c.match.test(text) && !c.exclude?.test(title));
    if (found) return found;
  }
  return undefined;
}
