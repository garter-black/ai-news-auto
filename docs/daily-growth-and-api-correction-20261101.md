# 2026-11-01 読者獲得とAPI特典記事の訂正

## 基準と判断

11月1日02時ごろ、Cloudflare Web Analytics の国=Japan・bot除外・全流入元で、10/31 00:00〜11/1 00:00 JST は12 visits / 19 page views（直前の同期間比 visits -81.82% と画面表示）。visit は個別の読者数ではなく、自分や除外しきれない機械等が混入しうる。11/1 00:00〜01:52 は同条件で0 visitsだが、夜間の途中値で反映遅れや少数ゆえ終日を予測しない。最多日の全履歴の数字は未確定。Search Console の直近24時間表示は15クリック・302表示、最終更新17.5時間前と表示。期間、時差、推定方法・更新遅れが異なるので Cloudflare と足さない。検索に表示される語の一部は匿名化され、ページ数値と開示クエリの合計は一致しない。

検索入口は `/articles/gemini-notebook-free-textbooks` が3クリック / 22表示、`/articles/ntt-4k-ai-video-surveillance-2026` が3 / 13、`/events/adobe-max-2026` が2 / 26。いずれも少数の暫定値で、改善の効果や因果関係は未確認。Gemini Notebookの記事に公式改称記事を根拠とした旧名NotebookLMの説明と、既存の使い方ガイドへの内部リンクを追加。タイトルと既存の日本提供未確認という条件を維持した。根拠 https://blog.google/innovation-and-ai/products/gemini-notebook/notebooklm-gemini-notebook/ と https://blog.google/innovation-and-ai/products/gemini-notebook/new-access-textbooks/ 。本日中のGoogle検索への反映は保証されない。

## 問題のあったAPI特典記事

`/articles/chatgpt-pro-business-api-credits-benefit` には、ChatGPTとAPIの決済が統一された、無料枠が全員に自動付与される、使い放題で追加料金はかからない、APIキー経由のCodexを含む全APIが対象、日本の契約者全員が今日から利用できる等の誤解を招く記載があった。公式 https://developers.openai.com/api/docs/guides/chatgpt-api-credits と照合し、冒頭に訂正を明記し、対象・7日条件・申請・初回日割り・有効期限・Businessの上限・クレジット残高とAPIの別請求・Codex除外を説明。日本の個別アカウントは未確認。未検証の他社比較と動画タイムスタンプを外した。URL、元動画リンク、広告・計測を保持。記事内の今後使われ得る投稿文を条件付きの正確な表現に直したが、既存投稿・予約・履歴は変更せず、新たなSNS投稿も行っていない。

2026-11-01 02:00 JST の記事生成定期実行は成功、変更後のコミットを実行しているが、記事候補0本だったため新しい品質判定のLLM実応答や短い記事の本番生成は未検証。投稿を増やす目的でワークフローを手動実行していない。

## 公開と検索通知

commit `634be64` の Cloudflare 公開workflowは成功。公開記事2件はHTTP 200で、Geminiの旧名とガイドへのリンク、ChatGPTの訂正文と別請求の文言をHTMLで確認。広告・外部リンクを開く操作は行っていない。Search Consoleへの再取得申請やGoogle側の再クロールは未確認。IndexNowには変更した2 URLだけを送信しHTTP 200受理を確認した（Bing等の取得・検索反映・流入増を意味しない）。既存のSNS予約は触っていない。
