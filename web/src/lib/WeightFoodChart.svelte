<script>
  // Weight trend and food dispensed on one chart: weight (median per bucket +
  // 7-day average) on the left axis in lbs, food dispensed as bars on the right
  // axis in cups — so you can read weight against intake. Raw weigh-ins are a
  // legend-toggleable scatter (hidden by default).
  import Chart from "./Chart.svelte";
  import {
    movingAverage,
    dayToLocalTime,
    bucketUnit,
    bucketBy,
    sum,
    median,
    rejectWeightOutliers,
  } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { weights, food } = $props();

  const RAW_NAME = "Raw weigh-ins";
  const DAY = 86400000;

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
    // Drop implausible weigh-ins before they taint the median trend / y-scale.
    const raw = rejectWeightOutliers(
      (weights?.raw ?? []).map((r) => [new Date(r.timestamp).getTime(), r.weight_lbs])
    );
    const allFood = (food?.daily ?? []).map((d) => [dayToLocalTime(d.date), d.cups]);

    // The feeder's history (years) usually predates the litter robot's, so weight
    // covers a shorter window. Anchor the combined view to the weight-tracked
    // period — otherwise the weight line collapses into a sliver at the right —
    // and bucket both series by that span. With no weight data, show food alone.
    let foodPts, unit, xMin;
    if (raw.length) {
      xMin = raw[0][0];
      const xMax = raw[raw.length - 1][0];
      foodPts = allFood.filter((p) => p[0] >= xMin);
      unit = bucketUnit(Math.max(0, (xMax - xMin) / DAY));
    } else {
      foodPts = allFood;
      xMin = undefined;
      const t = allFood.map((p) => p[0]);
      unit = bucketUnit(t.length > 1 ? (Math.max(...t) - Math.min(...t)) / DAY : 0);
    }

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

<Chart {option} />
