<script>
  import Chart from "./Chart.svelte";
  import {
    movingAverage,
    fmtLbs,
    bucketUnit,
    bucketBy,
    median,
    rejectPartialWeighins,
  } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { weights, showRaw } = $props();

  const DAY = 86400000;

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
    // Drop partial weigh-ins (e.g. a lone 3.3 lbs) up front, so they taint
    // neither the per-bucket median trend nor the raw scatter / y-axis scale.
    const raw = rejectPartialWeighins(
      weights.raw.map((r) => [new Date(r.timestamp).getTime(), r.weight_lbs])
    );

    // The curated per-pet series is short-lived server-side (~days), so the
    // long-term trend comes from the raw weigh-ins: bucket by range and take the
    // MEDIAN per bucket.
    let unit = "day";
    if (raw.length > 1) {
      const span = (raw[raw.length - 1][0] - raw[0][0]) / DAY;
      unit = bucketUnit(span);
    }
    const trend = bucketBy(raw, unit, median);

    const series = [
      {
        name: unit === "day" ? "Weight (daily median)" : `Weight (${unit}ly median)`,
        type: "line",
        data: trend,
        smooth: 0.25,
        symbolSize: 5,
        itemStyle: { color: c.accent },
        lineStyle: { color: c.accent, width: 2 },
      },
    ];
    // A 7-day moving average is only meaningful at daily granularity.
    if (unit === "day") {
      series.push({
        name: "7-day avg",
        type: "line",
        data: movingAverage(trend),
        smooth: 0.35,
        showSymbol: false,
        itemStyle: { color: c.accent2 },
        lineStyle: { color: c.accent2, width: 2, type: [6, 4] },
      });
    }
    if (showRaw && raw.length) {
      series.push({
        name: "Raw weigh-ins",
        type: "scatter",
        data: raw,
        symbolSize: 4,
        itemStyle: { color: c.rawDot },
      });
    }

    const base = baseOption(c);
    return {
      ...base,
      tooltip: { ...base.tooltip, valueFormatter: (v) => fmtLbs(v) },
      xAxis: timeAxis(c),
      yAxis: {
        ...valueAxis(c),
        scale: true,
        axisLabel: { ...valueAxis(c).axisLabel, formatter: "{value} lb" },
      },
      series,
    };
  });
</script>

<Chart {option} />
