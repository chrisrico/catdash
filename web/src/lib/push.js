// Web Push from this browser, set up on its own (App.svelte calls ensurePush
// once controls are known to be on): register /sw.js, subscribe with the
// server's VAPID public key and hand the subscription to the server
// (POST /api/push/subscribe), which pushes a welcome to a browser it has not
// seen before. There is no button: every browser that opens the dashboard is
// one the stuck-robot watchdog's "check the robot" should reach. Turning them
// off is the browser's own site setting (block notifications), which revokes
// the subscription; the next send then gets a 410 and the server drops the row.
//
// Safari and Firefox only show the permission prompt from a user gesture, so
// when permission has not been decided the ask waits for the first click or
// keypress on the page. Once granted, later loads subscribe silently — and
// re-post the subscription every time, which is idempotent and heals a row
// the server retired after a transient failure.

import { fetchJSON } from "./api.js";

export const pushSupported = () =>
  typeof window !== "undefined" &&
  "serviceWorker" in navigator &&
  "PushManager" in window &&
  "Notification" in window;

// The applicationServerKey the browser wants: the base64url string the server
// publishes, decoded to the raw 65-byte point.
export function urlBase64ToUint8Array(base64url) {
  const padded = base64url + "=".repeat((4 - (base64url.length % 4)) % 4);
  const binary = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

// Resolves once the user has interacted with the page — the gesture the
// permission prompt needs.
export const firstGesture = (target = window) =>
  new Promise((resolve) => {
    const done = () => {
      target.removeEventListener("pointerdown", done);
      target.removeEventListener("keydown", done);
      resolve();
    };
    target.addEventListener("pointerdown", done, { once: true });
    target.addEventListener("keydown", done, { once: true });
  });

// Subscribe (or re-use the browser's existing subscription) and tell the
// server. Returns the server's answer; throws with a message fit for the
// status line.
export async function subscribe() {
  const { public_key } = await fetchJSON("/api/push");
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  await navigator.serviceWorker.ready;
  const sub =
    (await reg.pushManager.getSubscription()) ||
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(public_key),
    }));
  const res = await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sub.toJSON()),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    await sub.unsubscribe().catch(() => {});
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

// The whole thing, on load. `notify(message, isError)` is how the outcome is
// shown — only a browser newly subscribed, or one that failed, hears about it.
export async function ensurePush({ notify = () => {}, gesture = firstGesture } = {}) {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  if (Notification.permission !== "granted") {
    await gesture();
    if ((await Notification.requestPermission()) !== "granted") return "denied";
  }
  try {
    const data = await subscribe();
    if (data.new) notify("Notifications on: this browser hears when a robot stays stuck in use");
    return "on";
  } catch (e) {
    console.warn("[catdash] push setup failed:", e);
    notify(`Notifications not on: ${e.message}`, true);
    return "failed";
  }
}
