const DAY = 86400000;

// Activity categories for the feed's multiselect filter (keys must match the
// server's db.ACTIVITY_CATEGORIES).
export const ACTIVITY_CATEGORIES = [
  { key: "weigh_in", label: "Weigh-ins" },
  { key: "clean_cycle", label: "Clean cycles" },
  { key: "cat_detected", label: "Cat detected" },
  { key: "litter_dispensed", label: "Litter dispensed" },
  { key: "fault", label: "Faults" },
  { key: "other", label: "Other" },
];

export async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${url}`);
  return res.json();
}

export const fmtLbs = (v) => (v == null ? "—" : `${Number(v).toFixed(2)} lbs`);
export const fmtCups = (v) =>
  v == null ? "—" : `${Number(v).toFixed(2)} cup${Number(v) === 1 ? "" : "s"}`;
export const fmtDateTime = (ts) =>
  new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

export function rangeToStart(range) {
  if (range === "all") return null;
  const d = new Date(Date.now() - Number(range) * DAY);
  return d.toISOString().slice(0, 10);
}

// A "YYYY-MM-DD" day bucket is a robot-local calendar day; parse it as LOCAL
// midnight so daily bars land on the right day (new Date("YYYY-MM-DD") is UTC).
export function dayToLocalTime(day) {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d).getTime();
}

// --- Shared time bucketing so every chart stays legible across ranges ---

// Pick a bucket granularity from the data's span in days.
export function bucketUnit(spanDays) {
  if (spanDays <= 100) return "day";
  if (spanDays <= 730) return "week";
  return "month";
}

// Human label for a bucket start, matching the active granularity. Used as the
// hover-tooltip header and the click-to-detail modal title.
export function fmtBucket(ms, unit) {
  const d = new Date(ms);
  if (unit === "month")
    return d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  if (unit === "week")
    return "Week of " + d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

// Floor a millisecond timestamp to the start of its day / ISO-week / month (local).
export function bucketStartMs(ms, unit) {
  const d = new Date(ms);
  if (unit === "month") return new Date(d.getFullYear(), d.getMonth(), 1).getTime();
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  if (unit === "week") day.setDate(day.getDate() - ((day.getDay() + 6) % 7)); // back to Monday
  return day.getTime();
}

// Group [ms, value] points into buckets and reduce each with `reduce(values[])`.
export function bucketBy(points, unit, reduce) {
  const groups = new Map();
  for (const [ms, v] of points) {
    if (v == null) continue;
    const t = bucketStartMs(ms, unit);
    (groups.get(t) ?? groups.set(t, []).get(t)).push(v);
  }
  return [...groups.entries()].sort((a, b) => a[0] - b[0]).map(([t, vs]) => [t, reduce(vs)]);
}

export const sum = (vs) => Math.round(vs.reduce((a, b) => a + b, 0) * 1000) / 1000;
export const median = (vs) => {
  const s = [...vs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// Reject biologically-implausible weigh-ins before charting. A cat's weight
// drifts slowly, so a real reading is always close to its time-neighbors;
// sensor artifacts are not — partial weigh-ins (only partway onto the scale)
// read far LOW, double/glitch weigh-ins read far HIGH. For each point we take
// the median of a window of nearby readings and drop it if it deviates more
// than `tol` from that LOCAL median. Using a rolling (not global) median tracks
// genuine long-term drift while still catching spikes in either direction; the
// window is large enough that a cluster of bad readings can't move the median.
// `points` are [ms, lbs] in time order; returns the same shape, filtered.
export function rejectWeightOutliers(points, { window = 15, tol = 0.15 } = {}) {
  if (points.length < 5) return points; // too few for a stable local center
  const vals = points.map((p) => p[1]);
  const n = vals.length;
  const half = window >> 1;
  return points.filter((_, i) => {
    const local = median(vals.slice(Math.max(0, i - half), Math.min(n, i + half + 1)));
    return !local || Math.abs(vals[i] - local) / local <= tol;
  });
}

// Time-based 7-day trailing moving average over curated readings.
export function movingAverage(points, windowDays = 7) {
  const win = windowDays * DAY;
  return points.map((p, i) => {
    const t = p[0];
    let sum = 0;
    let n = 0;
    for (let j = i; j >= 0 && t - points[j][0] <= win; j--) {
      sum += points[j][1];
      n++;
    }
    return [t, sum / n];
  });
}
