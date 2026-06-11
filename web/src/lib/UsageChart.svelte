<script>
  import Chart from "./Chart.svelte";
  import { dayToLocalTime } from "./api.js";
  import { COLORS, baseOption, timeAxis, valueAxis } from "./echarts.js";

  let { usage } = $props();

  const option = $derived.by(() => ({
    ...baseOption,
    xAxis: timeAxis,
    yAxis: { ...valueAxis, minInterval: 1 },
    series: [
      {
        name: "Clean cycles",
        type: "bar",
        data: usage.map((d) => [dayToLocalTime(d.date), d.cycles]),
        barMaxWidth: 28,
        itemStyle: { color: "rgba(76,194,255,0.55)", borderColor: COLORS.accent },
      },
      {
        name: "Weigh-ins",
        type: "line",
        data: usage.map((d) => [dayToLocalTime(d.date), d.weighins]),
        smooth: 0.3,
        showSymbol: false,
        itemStyle: { color: COLORS.good },
        lineStyle: { color: COLORS.good, width: 2 },
      },
    ],
  }));
</script>

<Chart {option} />
