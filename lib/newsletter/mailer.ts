/**
 * メール送信。設定されている方を使う（どちらも無ければ送らずに not-configured を返す）。
 * - Cloudflare Email Service（REST）: CF_EMAIL_TOKEN + CLOUDFLARE_ACCOUNT_ID
 * - Resend: RESEND_API_KEY
 * scripts/newsletter.py と同じ優先順。
 */
import type { NewsletterEnv } from "@/lib/newsletter/store";

export type Mail = {
  to: string;
  subject: string;
  html: string;
  text: string;
  headers?: Record<string, string>;
};

export type SendResult = { sent: true; provider: string } | { sent: false; reason: string };

const DEFAULT_FROM = "news@ai-tech-express.jp";
const FROM_NAME = "サイソクニュース";

export function mailerConfigured(env: NewsletterEnv): boolean {
  return Boolean((env.CF_EMAIL_TOKEN && env.CLOUDFLARE_ACCOUNT_ID) || env.RESEND_API_KEY);
}

export async function sendMail(env: NewsletterEnv, mail: Mail): Promise<SendResult> {
  const from = env.NEWSLETTER_FROM || DEFAULT_FROM;
  try {
    if (env.CF_EMAIL_TOKEN && env.CLOUDFLARE_ACCOUNT_ID) {
      const res = await fetch(
        `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/email/sending/send`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${env.CF_EMAIL_TOKEN}`, "Content-Type": "application/json" },
          body: JSON.stringify({
            to: mail.to,
            from: { address: from, name: FROM_NAME },
            subject: mail.subject,
            html: mail.html,
            text: mail.text,
            headers: mail.headers,
          }),
        },
      );
      if (!res.ok) return { sent: false, reason: `cloudflare ${res.status} ${(await res.text()).slice(0, 200)}` };
      return { sent: true, provider: "cloudflare" };
    }
    if (env.RESEND_API_KEY) {
      const res = await fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          from: `${FROM_NAME} <${from}>`,
          to: [mail.to],
          subject: mail.subject,
          html: mail.html,
          text: mail.text,
          headers: mail.headers,
        }),
      });
      if (!res.ok) return { sent: false, reason: `resend ${res.status} ${(await res.text()).slice(0, 200)}` };
      return { sent: true, provider: "resend" };
    }
  } catch (error) {
    return { sent: false, reason: String(error).slice(0, 200) };
  }
  return { sent: false, reason: "not-configured" };
}
