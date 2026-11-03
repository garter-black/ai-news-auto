# 本番ページを機械で開くときの決まり（エージェント・CI 向け）

本番（https://ai-tech-express.jp）のページには A8 などの提携リンクと表示計測の画像（`*.a8.net/0.gif`）が入っている。
自動ブラウザで開いたり、提携リンクを押したりすると、ASP の表示回数・クリック数が人以外で水増しされ、
成果ゼロのクリックが積み上がって不自然な数字に見える（2026-10-27: A8 クリック 134 回に対し、人の訪問は 61）。

## 守ること

1. **本番を実ブラウザで開くときは、次のドメインへの通信を止める**（スクショ・見た目の確認・読みやすさの検査すべて）
   - `*.a8.net`（`px.a8.net` `rpx.a8.net` `www*.a8.net` の計測画像・バナー）
   - `*.afl.rakuten.co.jp`（楽天アフィリエイト）
   - `amazon.co.jp` `*.amazon.co.jp` `amzn.to`（Amazonアソシエイト。tag=saisokuai-22 のリンク）
   - `*.amazon-adsystem.com`
   - `*.googlesyndication.com` `*.doubleclick.net` `*.googleadservices.com` `adservice.google.com` `*.adtrafficquality.google`（AdSense）
2. **提携リンク（`rel` に `sponsored` を含む a）は押さない・URL を取りに行かない**（curl・WebFetch・リンク切れ検査も禁止）。
   リンク先の確認は管理画面（A8 など）で行う。
3. **自社アクセスのしるしを付ける**: 本番の URL には `?_internal=1` を付けるか、ヘッダー `X-Saisoku-Internal: 1` を付ける（curl・Playwright の `extraHTTPHeaders`・Lighthouse の `--extra-headers`）。記事への流入数（`scripts/inflow_report.py`）と提携リンクのクリック数から除くため。
4. 見た目の確認はできるだけローカル（`npm run dev`）で行う。`next dev` では計測画像を出さない（`VERCEL_ENV` が production のときだけ出す）。
   `npm run cf:preview`（wrangler dev）は wrangler.jsonc の `VERCEL_ENV=production` で計測画像が出るので、1. と同じく止めること。

## 止め方

- **Chrome を自分で起動する場合**（いちばん確実。新しいタブ・ポップアップも止まる）:
  `--host-resolver-rules="MAP a8.net ~NOTFOUND, MAP *.a8.net ~NOTFOUND, MAP *.afl.rakuten.co.jp ~NOTFOUND, MAP amazon.co.jp ~NOTFOUND, MAP *.amazon.co.jp ~NOTFOUND, MAP amzn.to ~NOTFOUND, MAP *.amazon-adsystem.com ~NOTFOUND, MAP *.googlesyndication.com ~NOTFOUND, MAP *.doubleclick.net ~NOTFOUND"`
- **スクショが欲しいだけなら** `npm run check:readability -- --paths /articles/<slug> --shots out/shots` を使う（上の止め方が入っている。scripts/check_readability.mjs）
- **Playwright**:
  ```js
  await context.route(/^https?:\/\/([^/]+\.)?(a8\.net|afl\.rakuten\.co\.jp|amazon\.co\.jp|amzn\.to|amazon-adsystem\.com|googlesyndication\.com|doubleclick\.net|googleadservices\.com)\//, (r) => r.abort());
  ```
- **通信を止められないブラウザ**（Claude のブラウザ画面・Chrome 拡張など）では本番の記事ページを開かない。開いた場合も PR 枠には触れない。

## 人のクリックの数え方

提携リンクのクリックは自前でも数えている（自動ブラウザ・クローラーは数えない）。
`python3 scripts/aff_stats.py --yesterday` で前日分が出る（A8 の数字と並べて見る）。仕組みは cloudflare/aff.ts。
記事に外から来た人（流入元・投稿ごと）は `python3 scripts/inflow_report.py --yesterday`。仕組みは cloudflare/inflow.ts。
