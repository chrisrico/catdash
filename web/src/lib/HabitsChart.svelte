<script>
  // Bathroom habits, à la Whisker+: how often, what time of day, and how long
  // the cat uses the box, to surface unusual behavior. Each raw weigh-in is one
  // visit (the cat got on the scale). Times are in the viewer's local time.
  // Visit COUNT comes from weights.raw; the duration RANGE (time-in-box) comes
  // from the backend (it needs the wait-time) as per-visit samples we bucket.
  import Chart from "./Chart.svelte";
  import Modal from "./Modal.svelte";
  import {
    bucketUnit,
    bucketBy,
    sum,
    bucketStartMs,
    fmtBucket,
    fmtLbs,
    fmtDateTime,
    median,
  } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { weights, duration = null } = $props();

  const DAY = 86400000;
  const MIN = 60;

  // Compact duration label for a single visit (seconds).
  const fmtDur = (sec) => (sec < 90 ? `${sec}s` : `${(sec / 60).toFixed(1)} min`);

  // Approximate time-in-box (median overall) for the summary line.
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

  // Bucket granularity (day/week/month) lifted out of the option closure so the
  // hover header and the click-to-detail modal floor timestamps the same way the
  // bars do.
  const unit = $derived.by(() => {
    const span = perDay.length > 1 ? (perDay[perDay.length - 1][0] - perDay[0][0]) / DAY : 0;
    return bucketUnit(span);
  });

  // --- Click-to-detail: the clicked bucket and the visits/durations within it ---
  let clickedMs = $state(null);
  const clickedBucket = $derived(clickedMs == null ? null : bucketStartMs(clickedMs, unit));

  const MATCH_WINDOW = 30 * 60 * 1000; // weigh-in → its clean cycle, in ms

  const detail = $derived.by(() => {
    if (clickedBucket == null) return null;
    const inBucket = (ms) => bucketStartMs(ms, unit) === clickedBucket;
    // A visit produces a weigh-in (weight) and, minutes later, a clean cycle
    // that yields the time-in-box sample — two separate series that share no
    // timestamp. Stitch them into one row per visit: pair each duration with the
    // nearest preceding weigh-in (within MATCH_WINDOW); leave weight or duration
    // blank when its half of the pair is missing.
    const weighIns = (weights?.raw ?? [])
      .map((r) => ({ ms: new Date(r.timestamp).getTime(), weight: r.weight_lbs }))
      .filter((w) => inBucket(w.ms))
      .sort((a, b) => a.ms - b.ms);
    const durs = (duration?.samples ?? [])
      .map(([t, sec]) => ({ ms: new Date(t).getTime(), sec }))
      .filter((d) => inBucket(d.ms))
      .sort((a, b) => a.ms - b.ms);

    const used = new Set();
    const rows = [];
    for (const d of durs) {
      let match = -1;
      for (let i = 0; i < weighIns.length; i++) {
        if (used.has(i)) continue;
        const w = weighIns[i];
        if (w.ms <= d.ms && d.ms - w.ms <= MATCH_WINDOW) match = i; // nearest preceding
      }
      if (match >= 0) {
        used.add(match);
        rows.push({ ms: weighIns[match].ms, weight: weighIns[match].weight, sec: d.sec });
      } else {
        rows.push({ ms: d.ms, weight: null, sec: d.sec });
      }
    }
    weighIns.forEach((w, i) => {
      if (!used.has(i)) rows.push({ ms: w.ms, weight: w.weight, sec: null });
    });
    rows.sort((a, b) => a.ms - b.ms);
    return { rows };
  });

  // Nearest-rank percentile (fine for the small per-bucket samples).
  const pct = (sorted, p) => (sorted.length ? sorted[Math.round(p * (sorted.length - 1))] : 0);

  // Group per-visit durations into [bucketMs, p10, median, p90] minutes.
  function bucketRanges(samples, unit) {
    const groups = new Map();
    for (const [ms, sec] of samples) {
      const t = bucketStartMs(ms, unit);
      (groups.get(t) ?? groups.set(t, []).get(t)).push(sec);
    }
    return [...groups.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([t, vals]) => {
        const s = [...vals].sort((a, b) => a - b);
        return [t, pct(s, 0.1) / MIN, median(s) / MIN, pct(s, 0.9) / MIN];
      });
  }

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
    const bucketMs = unit === "month" ? 2629800000 : unit === "week" ? 604800000 : DAY;

    const samples = (duration?.samples ?? []).map(([t, s]) => [new Date(t).getTime(), s]);
    const ranges = bucketRanges(samples, unit);

    const series = [
      {
        name: unit === "day" ? "Visits/day" : `Visits/${unit}`,
        type: "bar",
        data: bucketBy(perDay, unit, sum),
        z: 1,
        barMaxWidth: 36,
        barMinHeight: 1,
        itemStyle: { color: c.barCycle, borderColor: c.accent },
      },
    ];

    if (ranges.length) {
      // A floating range bar (p10–p90) with a median tick, per bucket, drawn on
      // the right axis (minutes). Custom renderItem so it doesn't fight the
      // visit-count bars for ECharts' automatic bar layout.
      series.push({
        name: "Time in box",
        type: "custom",
        yAxisIndex: 1,
        data: ranges,
        z: 3,
        itemStyle: { color: c.accent2 },
        encode: { x: 0, y: [1, 2, 3], tooltip: [1, 2, 3] },
        renderItem: (params, api) => {
          const t = api.value(0);
          const low = api.coord([t, api.value(1)]);
          const med = api.coord([t, api.value(2)]);
          const high = api.coord([t, api.value(3)]);
          const x = low[0];
          const halfW = Math.max(3, Math.min(8, api.size([bucketMs, 0])[0] * 0.16));
          return {
            type: "group",
            children: [
              {
                type: "rect",
                shape: {
                  x: x - halfW,
                  y: high[1],
                  width: halfW * 2,
                  height: Math.max(1, low[1] - high[1]),
                  r: 3,
                },
                style: { fill: c.accent2, opacity: 0.28 },
              },
              {
                type: "line",
                shape: { x1: x - halfW, y1: med[1], x2: x + halfW, y2: med[1] },
                style: { stroke: c.accent2, lineWidth: 2 },
              },
            ],
          };
        },
      });
    }

    const base = baseOption(c);
    return {
      ...base,
      grid: { left: 46, right: 48, top: 28, bottom: 28 },
      tooltip: {
        ...base.tooltip,
        formatter: (ps) => {
          const arr = Array.isArray(ps) ? ps : [ps];
          const head = arr.length ? fmtBucket(bucketStartMs(arr[0].axisValue, unit), unit) : "";
          const lines = [];
          for (const p of arr) {
            if (p.seriesType === "custom") {
              const v = p.value; // [t, p10, median, p90] (minutes)
              lines.push(
                `${p.marker} In box: <b>${v[2].toFixed(1)}m</b> ` +
                  `<span style="opacity:.6">(${v[1].toFixed(1)}–${v[3].toFixed(1)}m)</span>`
              );
            } else {
              const val = Array.isArray(p.value) ? p.value[1] : p.value;
              lines.push(`${p.marker} ${p.seriesName}: <b>${val}</b>`);
            }
          }
          return head + "<br/>" + lines.join("<br/>");
        },
      },
      xAxis: timeAxis(c),
      yAxis: [
        {
          ...valueAxis(c),
          minInterval: 1,
          name: "visits",
          nameTextStyle: { color: c.accent },
        },
        {
          ...valueAxis(c),
          min: 0,
          name: "min",
          nameTextStyle: { color: c.accent2 },
          splitLine: { show: false },
          axisLabel: { ...valueAxis(c).axisLabel, formatter: "{value}m" },
        },
      ],
      series,
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
  <Chart {option} onBucketClick={(ms) => (clickedMs = ms)} />
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

{#if detail}
  <Modal title={fmtBucket(clickedBucket, unit)} onClose={() => (clickedMs = null)}>
    {#if !detail.rows.length}
      <div class="empty">No visits in this period.</div>
    {:else}
      <p class="modal-sub">Visits ({detail.rows.length})</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>When</th><th class="num">Weight</th><th class="num">Time in box</th></tr></thead>
          <tbody>
            {#each detail.rows as row}
              <tr>
                <td>{fmtDateTime(row.ms)}</td>
                <td class="num">
                  {#if row.weight != null}<span class="pill weight">{fmtLbs(row.weight)}</span>{/if}
                </td>
                <td class="num">{row.sec != null ? fmtDur(row.sec) : ""}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </Modal>
{/if}
