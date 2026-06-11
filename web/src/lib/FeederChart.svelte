<script>
  import Chart from "./Chart.svelte";
  import { dayToLocalTime } from "./api.js";
  import { COLORS, baseOption, timeAxis, valueAxis } from "./echarts.js";

  let { food } = $props();

  const option = $derived.by(() => ({
    ...baseOption,
    xAxis: timeAxis,
    yAxis: [
      {
        ...valueAxis,
        name: "cups",
        nameTextStyle: { color: COLORS.muted },
      },
      {
        ...valueAxis,
        max: 100,
        splitLine: { show: false },
        axisLabel: { ...valueAxis.axisLabel, formatter: "{value}%" },
      },
    ],
    series: [
      {
        name: "Food dispensed (cups)",
        type: "bar",
        data: food.daily.map((d) => [dayToLocalTime(d.date), d.cups]),
        barMaxWidth: 48,
        itemStyle: { color: "rgba(240,136,62,0.55)", borderColor: COLORS.accent2 },
      },
      {
        name: "Hopper level (%)",
        type: "line",
        yAxisIndex: 1,
        step: "end",
        data: food.levels.map((l) => [new Date(l.timestamp).getTime(), l.level]),
        symbolSize: 5,
        itemStyle: { color: COLORS.accent },
        lineStyle: { color: COLORS.accent, width: 2 },
      },
    ],
  }));
</script>

<Chart {option} />
