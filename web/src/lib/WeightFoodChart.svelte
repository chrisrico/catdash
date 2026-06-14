<script>
  // Weight trend and food dispensed on one chart: weight (median per bucket +
  // 7-day average) on the left axis in lbs, food dispensed as bars on the right
  // axis in cups — so you can read weight against intake. Raw weigh-ins are a
  // legend-toggleable scatter (hidden by default). Hovering a bucket shows the
  // period's values; clicking one opens a modal listing every weigh-in and food
  // entry that falls in it.
  import Chart from "./Chart.svelte";
  import Modal from "./Modal.svelte";
  import {
    movingAverage,
    dayToLocalTime,
    bucketUnit,
    bucketBy,
    bucketStartMs,
    fmtBucket,
    fmtLbs,
    fmtCups,
    fmtDateTime,
    sum,
    median,
    rejectWeightOutliers,
  } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { weights, food } = $props();

  const RAW_NAME = "Raw weigh-ins";
  const DAY = 86400000;

  // Outlier-rejected weigh-ins as [ms, lbs] — the basis for the trend line and
  // the x-window. The feeder's history (years) usually predates the litter
  // robot's, so weight covers a shorter window; we anchor the combined view to
  // the weight-tracked period (else the weight line collapses into a sliver) and
  // bucket both series by that span. With no weight data, show food alone.
  const raw = $derived(
    rejectWeightOutliers(
      (weights?.raw ?? []).map((r) => [new Date(r.timestamp).getTime(), r.weight_lbs])
    )
  );
  const allFood = $derived((food?.daily ?? []).map((d) => [dayToLocalTime(d.date), d.cups]));

  const xMin = $derived(raw.length ? raw[0][0] : undefined);
  const unit = $derived.by(() => {
    if (raw.length) {
      const xMax = raw[raw.length - 1][0];
      return bucketUnit(Math.max(0, (xMax - xMin) / DAY));
    }
    const t = allFood.map((p) => p[0]);
    return bucketUnit(t.length > 1 ? (Math.max(...t) - Math.min(...t)) / DAY : 0);
  });
  const foodPts = $derived(xMin != null ? allFood.filter((p) => p[0] >= xMin) : allFood);

  // --- Click-to-detail: the bucket the user clicked, and its rows ---
  let clickedMs = $state(null);
  const bucketMs = $derived(clickedMs == null ? null : bucketStartMs(clickedMs, unit));

  const detail = $derived.by(() => {
    if (bucketMs == null) return null;
    const inBucket = (ms) => bucketStartMs(ms, unit) === bucketMs;
    // All raw weigh-ins (not outlier-filtered) — this is the full drill-down.
    const weighIns = (weights?.raw ?? [])
      .map((r) => [new Date(r.timestamp).getTime(), r.weight_lbs])
      .filter(([ms]) => inBucket(ms))
      .sort((a, b) => a[0] - b[0]);
    // Individual meals/snacks (not the daily total), same window as the bars.
    const meals = (food?.feedings ?? [])
      .map((f) => ({
        ms: new Date(f.timestamp).getTime(),
        name: f.name,
        type: f.type,
        cups: f.amount_cups,
      }))
      .filter((m) => inBucket(m.ms) && (xMin == null || m.ms >= xMin))
      .sort((a, b) => a.ms - b.ms);
    return { weighIns, meals };
  });

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);

    const trend = bucketBy(raw, unit, median);
    const foodBars = bucketBy(foodPts, unit, sum);

    // Colour language: weight is blue (median = bold solid line), its 7-day
    // average is orange (dashed overlay), food is green on its own axis, and raw
    // weigh-ins are faint neutral dots. z-order keeps the lines above the bars.
    const series = [
      {
        name: unit === "day" ? "Weight (daily median)" : `Weight (${unit}ly median)`,
        type: "line",
        data: trend,
        z: 3,
        smooth: 0.25,
        symbolSize: 5,
        itemStyle: { color: c.accent },
        lineStyle: { color: c.accent, width: 2.6 },
      },
    ];
    if (unit === "day") {
      series.push({
        name: "7-day avg",
        type: "line",
        data: movingAverage(trend),
        z: 3,
        smooth: 0.35,
        showSymbol: false,
        itemStyle: { color: c.accent2 },
        lineStyle: { color: c.accent2, width: 2, type: [6, 4] },
      });
    }
    series.push({
      name: `Food (cups/${unit})`,
      type: "bar",
      yAxisIndex: 1,
      data: foodBars,
      z: 1,
      barMaxWidth: 26,
      barMinHeight: 1,
      itemStyle: { color: c.barFood, borderColor: c.good },
    });
    if (raw.length) {
      series.push({
        name: RAW_NAME,
        type: "scatter",
        data: raw,
        z: 2,
        symbolSize: 4,
        itemStyle: { color: c.rawDot },
      });
    }

    const base = baseOption(c);
    return {
      ...base,
      legend: { ...base.legend, selected: { [RAW_NAME]: false } },
      tooltip: {
        ...base.tooltip,
        formatter: (ps) => {
          const arr = Array.isArray(ps) ? ps : [ps];
          if (!arr.length) return "";
          const head = fmtBucket(bucketStartMs(arr[0].axisValue, unit), unit);
          const lines = arr.map((p) => {
            const v = Array.isArray(p.value) ? p.value[1] : p.value;
            const txt = p.seriesType === "bar" ? fmtCups(v) : fmtLbs(v);
            return `${p.marker} ${p.seriesName}: <b>${txt}</b>`;
          });
          return head + "<br/>" + lines.join("<br/>");
        },
      },
      xAxis: { ...timeAxis(c), min: xMin },
      yAxis: [
        {
          ...valueAxis(c),
          scale: true,
          name: "lbs",
          nameTextStyle: { color: c.accent },
          axisLabel: { ...valueAxis(c).axisLabel, formatter: "{value} lb" },
        },
        {
          ...valueAxis(c),
          name: "cups",
          nameTextStyle: { color: c.good },
          splitLine: { show: false },
        },
      ],
      series,
    };
  });
</script>

<Chart {option} onBucketClick={(ms) => (clickedMs = ms)} />

{#if detail}
  <Modal title={fmtBucket(bucketMs, unit)} onClose={() => (clickedMs = null)}>
    {#if !detail.weighIns.length && !detail.meals.length}
      <div class="empty">No weigh-ins or feedings in this period.</div>
    {:else}
      {#if detail.weighIns.length}
        <p class="modal-sub">Weigh-ins ({detail.weighIns.length})</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>When</th><th class="num">Weight</th></tr></thead>
            <tbody>
              {#each detail.weighIns as [ms, lbs]}
                <tr>
                  <td>{fmtDateTime(ms)}</td>
                  <td class="num"><span class="pill weight">{fmtLbs(lbs)}</span></td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
      {#if detail.meals.length}
        <p class="modal-sub">Food dispensed ({detail.meals.length})</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Meal</th><th class="num">Dispensed</th></tr></thead>
            <tbody>
              {#each detail.meals as m}
                <tr>
                  <td>{fmtDateTime(m.ms)}</td>
                  <td>{m.name || (m.type === "snack" ? "Snack" : "Meal")}</td>
                  <td class="num">{fmtCups(m.cups)}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </Modal>
{/if}
