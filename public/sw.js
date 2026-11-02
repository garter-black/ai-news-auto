/*
 * サイソクニュースの Service Worker。速報のプッシュ通知を表示し、押したら記事を開くだけ。
 * ページの読み込み・キャッシュには一切関わらない（fetch のハンドラを置かない。古いページを返さないため）。
 * 送信側は cloudflare/push.ts、登録の画面は components/PushOptIn.tsx。
 */
const SITE = self.location.origin;

/** このサイトの URL だけ開く（ほかのサイトの URL が来ても、このサイトの同じパスにする） */
function safeUrl(value) {
  try {
    const url = new URL(value || "/", SITE);
    return url.origin === SITE ? url.href : SITE + url.pathname + url.search;
  } catch {
    return SITE + "/";
  }
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "サイソクニュース";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      icon: data.icon || "/icons/icon-192.png",
      badge: data.badge || "/icons/badge-96.png",
      // 同じ記事の通知は1つにまとめる
      tag: data.tag || "saisoku",
      lang: "ja",
      data: { url: safeUrl(data.url) },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = safeUrl(event.notification.data && event.notification.data.url);
  event.waitUntil(
    (async () => {
      const list = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      // 同じ記事を開いているタブがあればそれを前に出す。無ければ新しいタブで開く（読んでいる別のページは書き換えない）
      const path = new URL(target).pathname;
      const same = list.find((c) => new URL(c.url).pathname === path);
      if (same && "focus" in same) return same.focus();
      return self.clients.openWindow(target);
    })(),
  );
});
