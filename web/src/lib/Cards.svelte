<script>
  import { fmtDateTime } from "./api.js";

  let { stats, weights = null, foodLevels = null } = $props();

  // Each raw weigh-in is one bathroom visit; report the average per local day.
  const visits = $derived.by(() => {
    const raw = weights?.raw ?? [];
    if (!raw.length) return null;
    const days = new Set(
      raw.map((r) => {
        const d = new Date(r.timestamp);
        return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      })
    );
    return { total: raw.length, perDay: raw.length / days.size };
  });

  const cards = $derived.by(() => {
    const w = stats.weight;
    const flt = stats.faults || { count: 0, last: null };
    return [
      {
        label: "Latest weight",
        value: w.latest != null ? w.latest.toFixed(2) : "—",
        unit: w.latest != null ? " lbs" : "",
        delta:
          w.change != null
            ? {
                cls: w.change > 0 ? "up" : w.change < 0 ? "down" : "flat",
                text: `${w.change > 0 ? "▲" : w.change < 0 ? "▼" : "■"} ${Math.abs(w.change).toFixed(2)} lbs over range`,
              }
            : null,
      },
      {
        label: "Bathroom visits",
        value: visits ? visits.perDay.toFixed(1) : "—",
        unit: visits ? " / day" : "",
        delta: visits ? { cls: "flat", text: `${visits.total} in range` } : null,
      },
      {
        label: "Faults",
        value: flt.count ?? 0,
        delta: flt.last
          ? { cls: "up", text: `last: ${flt.last.action} · ${fmtDateTime(flt.last.timestamp)}` }
          : { cls: "flat", text: "none recorded" },
      },
    ];
  });

  // Food-hopper level as a compact sparkline (it's the feeder's food, not litter).
  const hopper = $derived.by(() => {
    const lv = foodLevels ?? [];
    if (!lv.length) return null;
    const pts = lv.map((l) => [new Date(l.timestamp).getTime(), l.level]);
    pts.push([Date.now(), pts[pts.length - 1][1]]); // carry the last reading to now
    const W = 120,
      H = 28;
    const xs = pts.map((p) => p[0]);
    const minx = Math.min(...xs),
      maxx = Math.max(...xs);
    const path = pts
      .map((p, i) => {
        const x = maxx > minx ? ((p[0] - minx) / (maxx - minx)) * W : (i / Math.max(1, pts.length - 1)) * W;
        const y = H - (p[1] / 100) * H;
        return `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return { level: pts[pts.length - 1][1], path, W, H };
  });
</script>

<section class="cards">
  {#each cards as c}
    <div class="card">
      <div class="label">{c.label}</div>
      <div class="value">{c.value}{#if c.unit}<small>{c.unit}</small>{/if}</div>
      {#if c.delta}
        <div class="delta {c.delta.cls}">{c.delta.text}</div>
      {/if}
    </div>
  {/each}
  {#if hopper}
    <div class="card">
      <div class="label">Food hopper</div>
      <div class="value">{hopper.level}<small>%</small></div>
      <svg class="sparkline" viewBox="0 0 {hopper.W} {hopper.H}" preserveAspectRatio="none">
        <path d={hopper.path} fill="none" />
      </svg>
    </div>
  {/if}
</section>
