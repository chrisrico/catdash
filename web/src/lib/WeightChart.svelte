<script>
  import Chart from "./Chart.svelte";
  import {
    movingAverage,
    fmtLbs,
    bucketUnit,
    bucketBy,
    median,
    rejectWeightOutliers,
  } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { weights } = $props();

  const RAW_NAME = "Raw weigh-ins";

  const DAY = 86400000;

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
    // Drop implausible weigh-ins (lone lows like 3.3 lbs, highs like 15.7) up
    // front, so they taint neither the per-bucket median trend nor the raw
    // scatter / y-axis scale.
    const raw = rejectWeightOutliers(
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
    // Raw weigh-ins are always a series so they can be toggled from the legend;
    // they start hidden (see legend.selected below) to keep the default view clean.
    if (raw.length) {
      series.push({
        name: RAW_NAME,
        type: "scatter",
        data: raw,
        symbolSize: 4,
        itemStyle: { color: c.rawDot },
      });
    }

    const base = baseOption(c);
    return {
      ...base,
      legend: { ...base.legend, selected: { [RAW_NAME]: false } },
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
