<script>
  import { fmtCups, fmtDateTime } from "./api.js";

  let { stats } = $props();

  const cards = $derived.by(() => {
    const w = stats.weight;
    const u = stats.usage;
    const busiest = u.busiest_day;
    const out = [
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
        label: "Weight range",
        value: w.min != null ? `${w.min.toFixed(1)}–${w.max.toFixed(1)}` : "—",
        unit: w.min != null ? " lbs" : "",
      },
      { label: "Weight readings", value: w.count ?? 0 },
      { label: "Total clean cycles", value: u.total_cycles ?? 0 },
      {
        label: "Avg cycles / day",
        value: u.avg_cycles != null ? u.avg_cycles.toFixed(1) : "—",
      },
      {
        label: "Busiest day",
        value: busiest ? busiest.cycles : "—",
        unit: busiest ? " cycles" : "",
        delta: busiest ? { cls: "flat", text: busiest.date } : null,
      },
    ];

    const f = stats.feeder || {};
    const lf = f.last_feeding;
    if (f.feedings || f.food_level != null) {
      out.push(
        {
          label: "Food level",
          value: f.food_level != null ? f.food_level : "—",
          unit: f.food_level != null ? "%" : "",
        },
        {
          label: "Fed (last 24h)",
          value: f.last_24h_cups != null ? Number(f.last_24h_cups).toFixed(2) : "—",
          unit: f.last_24h_cups != null ? " cups" : "",
          delta: f.last_24h_feedings
            ? {
                cls: "flat",
                text: `${f.last_24h_feedings} feeding${f.last_24h_feedings === 1 ? "" : "s"}`,
              }
            : null,
        },
        {
          label: "Last feeding",
          value: lf ? lf.name : "—",
          delta: lf
            ? { cls: "flat", text: `${fmtCups(lf.amount_cups)} · ${fmtDateTime(lf.timestamp)}` }
            : null,
        }
      );
    }
    return out;
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
</section>
