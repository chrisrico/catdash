<script>
  // Bathroom habits, à la Whisker+: how often and what time of day the cat uses
  // the box, to surface unusual behavior. Each raw weigh-in is one visit (the cat
  // got on the scale). Everything is computed in the viewer's local time.
  import Chart from "./Chart.svelte";
  import { bucketUnit, bucketBy, sum } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { weights, duration = null } = $props();

  const DAY = 86400000;

  // Approximate time-in-box (median), from the backend (needs the wait-time).
  const durationLabel = $derived.by(() => {
    const sec = duration?.median_sec;
    if (sec == null) return null;
    return sec < 90 ? `~${sec}s` : `~${(sec / 60).toFixed(1)} min`;
  });

  const visits = $derived((weights?.raw ?? []).map((r) => new Date(r.timestamp).getTime()));

  const localDayKey = (ms) => {
    const d = new Date(ms);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  };

  // [dayMs, count] per local calendar day.
  const perDay = $derived.by(() => {
    const m = new Map();
    for (const ms of visits) {
      const k = localDayKey(ms);
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => a[0] - b[0]);
  });

  // 24 counts, visits by hour of day.
  const byHour = $derived.by(() => {
    const arr = Array(24).fill(0);
    for (const ms of visits) arr[new Date(ms).getHours()]++;
    return arr;
  });
  const hourMax = $derived(Math.max(1, ...byHour));

  const summary = $derived.by(() => {
    const total = visits.length;
    const days = perDay.length || 1;
    return { total, days, avg: total / days, peak: byHour.indexOf(Math.max(...byHour)) };
  });

  const fmtHour = (h) => `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? "a" : "p"}`;

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
    const span =
      perDay.length > 1 ? (perDay[perDay.length - 1][0] - perDay[0][0]) / DAY : 0;
    const unit = bucketUnit(span);
    return {
      ...baseOption(c),
      grid: { left: 46, right: 24, top: 24, bottom: 28 },
      xAxis: timeAxis(c),
      yAxis: {
        ...valueAxis(c),
        minInterval: 1,
        name: "visits",
        nameTextStyle: { color: c.muted },
      },
      series: [
        {
          name: unit === "day" ? "Visits/day" : `Visits/${unit}`,
          type: "bar",
          data: bucketBy(perDay, unit, sum),
          barMaxWidth: 36,
          barMinHeight: 1,
          itemStyle: { color: c.barCycle, borderColor: c.accent },
        },
      ],
    };
  });
</script>

{#if summary.total === 0}
  <div class="empty">No bathroom visits recorded in this range.</div>
{:else}
  <p class="habits-summary">
    <b>{summary.avg.toFixed(1)}</b> visits/day · busiest around <b>{fmtHour(summary.peak)}</b>{#if durationLabel}{" · "}<b>{durationLabel}</b> in box{/if}
    <span class="habits-sub">· {summary.total} visits over {summary.days} days</span>
  </p>
  <Chart {option} />
  <div class="hour-strip" aria-label="visits by time of day">
    {#each byHour as n, h}
      <div class="hour-col" title="{fmtHour(h)} — {n} visit{n === 1 ? '' : 's'}">
        <div class="hour-bar" style="height:{Math.round((n / hourMax) * 100)}%"></div>
      </div>
    {/each}
  </div>
  <div class="hour-axis">
    <span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>11p</span>
  </div>
{/if}
