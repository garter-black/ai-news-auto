/** 登録確認メールの文面（配信本体は scripts/newsletter.py が組み立てる） */
import type { Mail } from "@/lib/newsletter/mailer";

export function confirmMail(to: string, confirmUrl: string): Mail {
  const subject = "【サイソクニュース】メール配信の登録を確認してください";
  const text = [
    "サイソクニュースのメール配信にご登録いただき、ありがとうございます。",
    "",
    "下のリンクを開くと登録が完了し、翌朝7時から配信が始まります。",
    confirmUrl,
    "",
    "お心当たりがない場合は、このメールを破棄してください。登録はされません。",
    "",
    "サイソクニュース https://ai-tech-express.jp",
  ].join("\n");
  const html = `<!doctype html><html lang="ja"><body style="margin:0;padding:24px;background:#f3f5f8;font-family:'Hiragino Sans','Hiragino Kaku Gothic ProN','Noto Sans JP',Meiryo,sans-serif;color:#111">
<div style="max-width:520px;margin:0 auto;background:#fff;border-top:3px solid #111;padding:28px 24px">
<p style="margin:0;font-size:12px;font-weight:700;letter-spacing:.08em;color:#465268">サイソクニュース</p>
<h1 style="margin:10px 0 0;font-size:20px;line-height:1.5">登録の確認</h1>
<p style="margin:16px 0 0;font-size:15px;line-height:1.9">メール配信にご登録いただき、ありがとうございます。下のボタンを押すと登録が完了し、翌朝7時から配信が始まります。</p>
<p style="margin:24px 0"><a href="${confirmUrl}" style="display:inline-block;background:#1d4a8f;color:#fff;text-decoration:none;font-weight:700;font-size:15px;padding:12px 22px">登録を完了する</a></p>
<p style="margin:0;font-size:12px;line-height:1.8;color:#465268">お心当たりがない場合は、このメールを破棄してください。登録はされません。</p>
</div></body></html>`;
  return { to, subject, text, html };
}
