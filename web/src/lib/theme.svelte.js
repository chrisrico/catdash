// The app follows the OS color scheme — no in-app override. `resolved` is what
// components read (CSS variables + ECharts palettes); it reacts live if the
// system theme flips. Lives in a .svelte.js module so the state is shared.
const mq =
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

let systemDark = $state(mq ? mq.matches : true);

mq?.addEventListener("change", (e) => (systemDark = e.matches));

export const themeState = {
  get resolved() {
    return systemDark ? "dark" : "light";
  },
};

// Synchronous read (no runes) so main.js can set <html data-theme> BEFORE first
// paint, avoiding a flash of the wrong theme.
export function initialResolvedTheme() {
  return mq && mq.matches ? "dark" : "light";
}
