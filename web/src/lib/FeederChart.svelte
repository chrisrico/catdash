<script>
  import Chart from "./Chart.svelte";
  import { dayToLocalTime, bucketUnit, bucketBy, sum } from "./api.js";
  import { COLORS, baseOption, timeAxis, valueAxis } from "./echarts.js";

  let { food } = $props();

  const DAY = 86400000;

  const option = $derived.by(() => {
    // Daily bars become 1px slivers across multi-year spans, so bucket the
    // dispensed totals by day / week / month depending on the visible range.
    const span =
      food.daily.length > 1
        ? (dayToLocalTime(food.daily[food.daily.length - 1].date) -
            dayToLocalTime(food.daily[0].date)) /
          DAY
        : 0;
    const unit = bucketUnit(span);
    const bars = bucketBy(
      food.daily.map((d) => [dayToLocalTime(d.date), d.cups]),
      unit,
      sum
    );

    // Hopper level is a step series recorded only when it changes; carry the
    // last reading forward to "now" so a steady level draws as a line, not a dot.
    const levels = food.levels.map((l) => [new Date(l.timestamp).getTime(), l.level]);
    if (levels.length) levels.push([Date.now(), levels[levels.length - 1][1]]);

    return {
      ...baseOption,
      xAxis: timeAxis,
      yAxis: [
        { ...valueAxis, name: "cups", nameTextStyle: { color: COLORS.muted } },
        {
          ...valueAxis,
          max: 100,
          splitLine: { show: false },
          axisLabel: { ...valueAxis.axisLabel, formatter: "{value}%" },
        },
      ],
      series: [
        {
          name: `Food dispensed (cups/${unit})`,
          type: "bar",
          data: bars,
          barMaxWidth: 36,
          barMinHeight: 1,
          itemStyle: { color: "rgba(240,136,62,0.6)", borderColor: COLORS.accent2 },
        },
        {
          name: "Hopper level (%)",
          type: "line",
          yAxisIndex: 1,
          step: "end",
          data: levels,
          showSymbol: true,
          symbolSize: 6,
          connectNulls: true,
          endLabel: {
            show: levels.length > 0,
            formatter: (p) => `${p.value[1]}%`,
            color: COLORS.accent,
          },
          itemStyle: { color: COLORS.accent },
          lineStyle: { color: COLORS.accent, width: 2 },
        },
      ],
    };
  });
</script>

<Chart {option} />
