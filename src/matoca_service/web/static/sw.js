// No fetch handler: account APIs and authenticated pages are never cached.
self.addEventListener("push", (event) => {
  let data;
  try { data = event.data?.json(); } catch { data = {}; }
  const title = typeof data?.title === "string" ? data.title : "順番待ちのお知らせ";
  const body = typeof data?.body === "string" ? data.body : "受付状況を確認してください";
  event.waitUntil(self.registration.showNotification(title, {
    body, icon: "/static/icon.svg", badge: "/static/icon.svg",
    tag: String(data?.id || "queue-update"), data: {url: safeUrl(data?.url)},
  }));
});

function safeUrl(value) {
  try {
    const url = new URL(typeof value === "string" ? value : "/", self.location.origin);
    if (url.origin === self.location.origin && !url.username && !url.password &&
        (url.pathname === "/" || /^\/merchants\/[a-z0-9_]+$/.test(url.pathname))) {
      return url.origin + url.pathname;
    }
  } catch { /* Invalid notification targets return to the homepage. */ }
  return self.location.origin + "/";
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = safeUrl(event.notification.data?.url);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: "window", includeUncontrolled: true});
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.navigate(url);
        await client.focus();
        return;
      }
    }
    await self.clients.openWindow(url);
  })());
});
