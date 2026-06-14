<script>
  import { fmtDateTime } from "./api.js";

  let { stats } = $props();

  const cards = $derived.by(() => {
    const w = stats.weight;
    const u = stats.usage;
    const flt = stats.faults || { count: 0, last: null };

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
        label: "Clean cycles",
        value: u.total_cycles ?? 0,
        delta:
          u.avg_cycles != null
            ? { cls: "flat", text: `${u.avg_cycles.toFixed(1)} / day avg` }
            : null,
      },
    ];

    out.push({
      label: "Faults",
      value: flt.count ?? 0,
      delta: flt.last
        ? { cls: "up", text: `last: ${flt.last.action} · ${fmtDateTime(flt.last.timestamp)}` }
        : { cls: "flat", text: "none recorded" },
    });

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
