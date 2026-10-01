// The push service worker (registered by web/src/lib/push.js, served at /sw.js
// by main.py). It does two things: show what the server pushed (push.py's
// payload), and open the dashboard when the notification is tapped. No
// caching, no fetch handling — the SPA is not offline-capable and this must
// never stand between it and the server.

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("push", (e) => {
  let data = {};
  try {
    data = e.data ? e.data.json() : {};
  } catch {
    data = { title: "Catdash", body: e.data && e.data.text() };
  }
  const title = data.title || "Catdash";
  const url = data.url || self.registration.scope;
  e.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "",
      tag: data.tag || "catdash",
      data: { url },
      // A repeat about the same robot replaces the earlier card (tag) but
      // still makes a sound.
      renotify: Boolean(data.tag),
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
      // A dashboard tab that is already open takes the link; otherwise a new one.
      const win = wins.find((w) => "focus" in w);
      if (win) return win.navigate(url).then((w) => (w || win).focus()).catch(() => win.focus());
      return self.clients.openWindow(url);
    })
  );
});
