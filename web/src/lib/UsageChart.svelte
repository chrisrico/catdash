<script>
  import Chart from "./Chart.svelte";
  import { dayToLocalTime, bucketUnit, bucketBy, sum } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { usage } = $props();

  const DAY = 86400000;

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
    const cyclesPts = usage.map((d) => [dayToLocalTime(d.date), d.cycles]);
    const weighinPts = usage.map((d) => [dayToLocalTime(d.date), d.weighins]);
    const span =
      usage.length > 1
        ? (dayToLocalTime(usage[usage.length - 1].date) - dayToLocalTime(usage[0].date)) / DAY
        : 0;
    const unit = bucketUnit(span);

    return {
      ...baseOption(c),
      xAxis: timeAxis(c),
      yAxis: { ...valueAxis(c), minInterval: 1 },
      series: [
        {
          name: "Clean cycles",
          type: "bar",
          data: bucketBy(cyclesPts, unit, sum),
          barMaxWidth: 36,
          barMinHeight: 1,
          itemStyle: { color: c.barCycle, borderColor: c.accent },
        },
        {
          name: "Weigh-ins",
          type: "line",
          data: bucketBy(weighinPts, unit, sum),
          smooth: 0.3,
          showSymbol: false,
          itemStyle: { color: c.good },
          lineStyle: { color: c.good, width: 2 },
        },
      ],
    };
  });
</script>

<Chart {option} />
