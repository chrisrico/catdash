// Tiny localStorage-backed persistence for dashboard UI selections.
// Pure helpers (no runes) so they can be used anywhere; the reactive write
// lives in a single $effect in App.svelte.
const PREFIX = "catdash:";

export function loadPersisted(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback; // unavailable / corrupt — fall back to the default
  }
}

export function savePersisted(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* ignore quota / private-mode errors */
  }
}
