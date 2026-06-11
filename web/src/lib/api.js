const DAY = 86400000;

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
