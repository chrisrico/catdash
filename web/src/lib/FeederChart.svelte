<script>
  import Chart from "./Chart.svelte";
  import { dayToLocalTime, bucketUnit, bucketBy, sum } from "./api.js";
  import { palette, baseOption, timeAxis, valueAxis } from "./echarts.js";
  import { themeState } from "./theme.svelte.js";

  let { food } = $props();

  const DAY = 86400000;

  const option = $derived.by(() => {
    const c = palette(themeState.resolved);
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
      ...baseOption(c),
      xAxis: timeAxis(c),
      yAxis: [
        { ...valueAxis(c), name: "cups", nameTextStyle: { color: c.muted } },
        {
          ...valueAxis(c),
          max: 100,
          splitLine: { show: false },
          axisLabel: { ...valueAxis(c).axisLabel, formatter: "{value}%" },
        },
      ],
      series: [
        {
          name: `Food dispensed (cups/${unit})`,
          type: "bar",
          data: bars,
          barMaxWidth: 36,
          barMinHeight: 1,
          itemStyle: { color: c.barFood, borderColor: c.accent2 },
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
            color: c.accent,
          },
          itemStyle: { color: c.accent },
          lineStyle: { color: c.accent, width: 2 },
        },
      ],
    };
  });
</script>

<Chart {option} />
