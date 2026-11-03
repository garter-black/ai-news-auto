import type { Bundle, RailBundle, StoryItem } from "@/components/story/SwipeDeck";
import { coverPath, getAllArticles, type ArticleSummary } from "@/lib/articles";
import { COMPANIES, companyOf } from "@/lib/companies";
import { easyGist } from "@/lib/general";
import { BROAD_SECTIONS } from "@/lib/sections";
import { byTierThenNewest, STORY_WINDOW_MS } from "@/lib/story-window";
import { freeLabel, japanLabel, listUsability, usability, USABILITY_LABEL, verdictOf } from "@/lib/verdict";

/*
 * 丸に入れるのは直近24時間の記事だけ。
 * トップは静的に作って次のデプロイまでキャッシュされるので、ここ（ビルド時）で一度しぼり、
 * 表示する側（components/story/SwipeDeck.tsx）でも閲覧時刻で同じ24時間を当て直す。
 */

/** 幅広い話題の丸の名前（丸の下は短くないと切れる） */
const GENRE_LABEL: Record<string, string> = {
  business: "ビジネス",
  investment: "投資・マネー",
  "real-estate": "不動産",
  "tech-gadget": "ガジェット",
  lifestyle: "暮らし",
  entertainment: "エンタメ",
  sports: "スポーツ",
  science: "科学・宇宙",
  public: "社会・行政",
};

/*
 * 用途別の丸は「見出し」で判定する（タグは「業務自動化」「DX」「開発ツール」のように広く付くので使わない）。
 * さらに、読む人が開発者・情シスだけの記事は、仕事・勉強・暮らしなどの丸に入れない。
 */

/** 自分の仕事で使える話（会議・資料・メール・営業・採用・集客など） */
const WORK =
  /仕事|業務効率|効率化|会議|議事録|文字起こし|Excel|Word|PowerPoint|Microsoft 365|Copilot|資料|PDF|メール|Slack|Teams|Notion|営業|採用|人事|集客|マーケティング|稟議|契約|業務アプリ|中小企業|個人事業|副業|ビジネスパーソン/;
/** 読者自身の勉強・資格・語学（AIモデルの「学習」は入れない） */
const STUDY = /勉強|教科書|教材|学習アプリ|学習AI|学習支援|授業|受験|学生|資格|語学|英会話|英語学習|学び直し|リスキリング|講座|講習/;
/** 暮らしの中で使う話 */
const LIFE = /旅行|観光|予約|買い物|ショッピング|献立|料理|レシピ|家事|家計|子育て|育児|健康|暮らし|生活/;
/** 画像・動画「を作る」話だけ（「解説動画を公開」のような記事を混ぜない） */
const CREATIVE = /(画像|動画|映像|イラスト|音楽)を?(生成|編集|作成|制作)|Sora|Veo|Imagen|Nano Banana|Midjourney|Firefly/;
const DEV = /開発者|エンジニア|開発ツール|API|SDK|GitHub|Codex|コーディング|プログラ/;
const AGENT = /エージェント|Agent/i;
/** 「新機能」ではない記事（事例・調査・解説）。「今使えるAI新機能」に入れない */
const NOT_FEATURE = /事例|調査|動向|理由|教訓|全貌|とは[？?]|発表予定/;
/** 企業のIT基盤の話（一般の人が「今使える」ものではない） */
const INFRA = /Azure|AWS|Bedrock|Kubernetes|基盤|SDK|API|半導体|データセンター|GPU|TPU|サーバー/;

/** 読む人が技術者・管理者だけか（一般の人・働く人が一緒に書かれていれば違う） */
const TECH_AUDIENCE = /開発|エンジニア|研究|インフラ|情シス|情報システム|IT|管理者|データ|セキュリティ|クラウド|AI基盤|AI導入|DX/;
const PEOPLE_AUDIENCE = /一般|ビジネス|社会人|ユーザー|利用者|個人|学生|経営者|経営層|営業|人事|採用|マーケ|資料|事務|会社員|店舗|中小企業|保護者|家庭|クリエイター|人$/;
function forTechOnly(a: ArticleSummary): boolean {
  const audience = a.verdict?.audience ?? "";
  return TECH_AUDIENCE.test(audience) && !PEOPLE_AUDIENCE.test(audience);
}

/**
 * 仕事とお金のニュース（AI以外のジャンル）。働く人の財布・副業・勤め先に関わるものだけを見出しで選ぶ。
 * 企業の決算・自社株買い・倒産件数・日銀の当座預金のような、家計に直接つながらない統計は入れない
 * （それは「ビジネス」の丸で読める）。
 */
const MONEY =
  /お金|マネー|家計|節約|資産|投資|NISA|iDeCo|証券|年金|老後|税|確定申告|インボイス|金利|ローン|物価|値上げ|円安|円高|給付|補助金|助成金|支援金|賃金|給与|給料|ボーナス|手取り|副業|転職|就職|働き方|中小企業|個人事業|起業|ポイ活/;

/** 記事の「ニュースの時刻」。元の発表時刻と掲載時刻の早いほう（未来の時刻は掲載時刻に寄せる） */
export function newsTime(a: ArticleSummary): number {
  const published = Date.parse(a.publishedAt);
  const generated = Date.parse(a.generatedAt);
  if (Number.isNaN(published)) return Number.isNaN(generated) ? 0 : generated;
  if (Number.isNaN(generated)) return published;
  return Math.min(published, generated);
}

function toItem(a: ArticleSummary, at: number): StoryItem {
  const ai = a.genreSlug === "ai";
  const c = companyOf(a);
  // 札（使える／一部／まだ）は製品・サービスの記事だけ（lib/verdict.ts の listUsability）
  const v = listUsability(a) ? verdictOf(a) ?? null : null;
  return {
    slug: a.slug,
    title: a.title,
    gist: easyGist(a),
    highlights: a.highlights.slice(0, 3),
    // 画像の無い web 記事は、企業色入りの独自カバーで代用する
    image: a.thumbnail || coverPath(a) || "",
    // AI以外の話題は企業ではなくジャンル名を出す
    company: ai ? c.name : GENRE_LABEL[a.genreSlug] ?? a.genre,
    color: ai ? c.color : "#465268",
    // 丸の24時間はこの時刻で数える（続報の丸は続報の時刻）
    at: new Date(at).toISOString(),
    usable: v ? usability(v) : null,
    usableLabel: v ? USABILITY_LABEL[usability(v)] : null,
    japan: v ? japanLabel(v) : null,
    free: v ? freeLabel(v.free) : null,
    price: v?.price ?? null,
  };
}

type Timed = { a: ArticleSummary; at: number };

/**
 * 並びの段。「最新のAIニュース」は常に先頭。
 * 次に読者（仕事・お金・副業・AIに関心のある20〜50代の働く人）の関心の中心、その他は後ろ。
 * 段の中は新しい記事がある丸から（開発者向けなどが先頭の次に来ないように）。
 */
const PINNED = new Set(["today"]);
const CORE = new Set([
  "money", "g-business", "go", "work", "free", "followup",
  "openai", "anthropic", "google", "microsoft", "nvidia", "agent",
]);
const tierOf = (key: string) => (PINNED.has(key) ? 0 : CORE.has(key) ? 1 : 2);
type Draft = { key: string; label: string; items: Timed[] };

/** 見た目が同じになる画像を見分けるキー（独自カバーは企業色が同じなら見分けがつかない） */
function lookKey(a: ArticleSummary): string {
  if (a.thumbnail) return a.thumbnail.replace(/(maxres|hq|mq|sd)default/, "");
  return a.genreSlug === "ai" ? `cover:${companyOf(a).slug}` : `cover:${a.genreSlug}`;
}

/**
 * 丸ごとに違う表紙を選ぶ。同じ記事・同じ見た目（同じ企業色の独自カバー）は使い回さず、
 * 写真のある記事を優先する。どうしても無ければ、まだ表紙にしていない記事で妥協する。
 */
function pickCovers(drafts: Draft[]): (ArticleSummary | undefined)[] {
  const usedSlug = new Set<string>();
  const usedLook = new Set<string>();
  return drafts.map((d) => {
    const items = d.items.map((x) => x.a);
    const fresh = items.filter((a) => !usedSlug.has(a.slug));
    const pick =
      fresh.find((a) => a.thumbnail && !usedLook.has(lookKey(a))) ??
      fresh.find((a) => !usedLook.has(lookKey(a))) ??
      fresh[0] ??
      items[0];
    if (pick) {
      usedSlug.add(pick.slug);
      usedLook.add(lookKey(pick));
    }
    return pick;
  });
}

/**
 * トップ上段の「ストーリーの丸」の束。
 * どの丸も直近24時間の記事だけを、本数の上限なしで入れる（0本の丸は出さない）。
 * 並びは固定ではなく、いちばん新しい記事が新しい丸から。
 * now は「今」（テスト用に渡せる）。閲覧時の当て直しは SwipeDeck 側で行う。
 */
export function getStoryBundles(now: number = Date.now()): Bundle[] {
  const all = getAllArticles().filter((a) => a.type !== "roundup" && a.hasEasy && !a.duplicate);
  const inWindow = (t: number) => t <= now && now - t < STORY_WINDOW_MS;
  const recent: Timed[] = all.map((a) => ({ a, at: newsTime(a) })).filter((x) => inWindow(x.at));
  const ai = recent.filter((x) => x.a.genreSlug === "ai");
  const aiWhere = (test: (a: ArticleSummary) => boolean) => ai.filter((x) => test(x.a));
  const forPeople = (a: ArticleSummary) => !forTechOnly(a);

  const followups: Timed[] = all
    .filter((a) => a.updatedAt)
    .map((a) => ({ a, at: Date.parse(a.updatedAt!) }))
    .filter((x) => inWindow(x.at));

  const candidates: Draft[] = [
    { key: "today", label: "最新のAIニュース", items: ai },
    {
      key: "go",
      label: "今使えるAI新機能",
      items: aiWhere(
        (a) => listUsability(a) === "go" && forPeople(a) && !NOT_FEATURE.test(a.title) && !INFRA.test(a.title),
      ),
    },
    {
      key: "free",
      label: "無料で試せるAI",
      items: aiWhere((a) => !!listUsability(a) && a.verdict?.free === true && a.verdict.status === "available" && forPeople(a) && !INFRA.test(a.title)),
    },
    { key: "work", label: "仕事を助けるAI", items: aiWhere((a) => WORK.test(a.title) && forPeople(a)) },
    { key: "study", label: "勉強に使えるAI", items: aiWhere((a) => STUDY.test(a.title) && forPeople(a)) },
    { key: "life", label: "暮らしと旅行のAI", items: aiWhere((a) => LIFE.test(a.title) && forPeople(a)) },
    { key: "creative", label: "画像・動画を作るAI", items: aiWhere((a) => CREATIVE.test(a.title) && forPeople(a)) },
    { key: "dev", label: "開発者向け", items: aiWhere((a) => DEV.test(`${a.title} ${a.verdict?.audience ?? ""}`)) },
    { key: "agent", label: "AIエージェント", items: aiWhere((a) => AGENT.test(a.title)) },
    {
      key: "money",
      label: "仕事とお金",
      items: recent.filter((x) => x.a.genreSlug !== "ai" && MONEY.test(x.a.title)),
    },
    { key: "followup", label: "続報あり", items: followups },
    // 企業別も24時間。名前だけにして、本数は丸の下に出る
    ...COMPANIES.map((c) => ({ key: c.slug, label: c.name, items: ai.filter((x) => companyOf(x.a).slug === c.slug) })),
    ...BROAD_SECTIONS.map((s) => ({
      key: `g-${s.slug}`,
      label: GENRE_LABEL[s.slug] ?? s.label,
      items: recent.filter((x) => x.a.genreSlug === s.slug),
    })),
  ];

  const drafts = candidates
    .map((d) => ({ ...d, items: [...d.items].sort((x, y) => y.at - x.at) }))
    .filter((d) => d.items.length > 0)
    // 段ごとに、新しい記事がある丸から（同じ時刻なら上の候補順）
    .map((d) => ({ ...d, tier: tierOf(d.key), newest: d.items[0].at }))
    .sort(byTierThenNewest);

  const covers = pickCovers(drafts);
  return drafts.map((d, i) => {
    const cover = covers[i];
    return {
      key: d.key,
      label: d.label,
      tier: d.tier,
      cover: cover ? cover.thumbnail || coverPath(cover) || "" : "",
      items: d.items.map((x) => toItem(x.a, x.at)),
    };
  });
}

/**
 * トップの丸の一覧用の軽い束（components/story/SwipeDeck.tsx の RailBundle）。
 * 要約・要点はトップの HTML に載せず、丸を開くときに /stories.json（app/stories.json/route.ts）から読む
 */
export function getStoryRailBundles(now: number = Date.now()): RailBundle[] {
  return getStoryBundles(now).map(({ items, ...rest }) => ({ ...rest, items: items.map(({ slug, title, image, at }) => ({ slug, title, image, at })) }));
}
