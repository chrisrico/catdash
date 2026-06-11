<script>
  import Chart from "./Chart.svelte";
  import { movingAverage, fmtLbs } from "./api.js";
  import { COLORS, baseOption, timeAxis, valueAxis } from "./echarts.js";

  let { weights, showRaw } = $props();

  const option = $derived.by(() => {
    const curated = weights.curated.map((r) => [
      new Date(r.timestamp).getTime(),
      r.weight_lbs,
    ]);
    const raw = weights.raw.map((r) => [
      new Date(r.timestamp).getTime(),
      r.weight_lbs,
    ]);

    const series = [
      {
        name: "Weight",
        type: "line",
        data: curated,
        smooth: 0.25,
        symbolSize: 6,
        itemStyle: { color: COLORS.accent },
        lineStyle: { color: COLORS.accent, width: 2 },
      },
      {
        name: "7-day avg",
        type: "line",
        data: movingAverage(curated),
        smooth: 0.35,
        showSymbol: false,
        itemStyle: { color: COLORS.accent2 },
        lineStyle: { color: COLORS.accent2, width: 2, type: [6, 4] },
      },
    ];
    if (showRaw && raw.length) {
      series.push({
        name: "Raw weigh-ins",
        type: "scatter",
        data: raw,
        symbolSize: 5,
        itemStyle: { color: "rgba(139,151,167,0.45)" },
      });
    }

    return {
      ...baseOption,
      tooltip: {
        ...baseOption.tooltip,
        valueFormatter: (v) => fmtLbs(v),
      },
      xAxis: timeAxis,
      yAxis: {
        ...valueAxis,
        scale: true,
        axisLabel: { ...valueAxis.axisLabel, formatter: "{value} lb" },
      },
      series,
    };
  });
</script>

<Chart {option} />
