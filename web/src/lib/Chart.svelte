<script>
  import echarts from "./echarts.js";

  // onBucketClick(ms) fires when the plot area is clicked, with the x-axis time
  // value (ms) under the cursor — the parent floors it to a bucket and drills in.
  let { option, onBucketClick } = $props();

  let el;
  let chart;

  // Lifecycle effect: create the chart, observe resizes, and wire the click
  // handler ONCE. It must run before the setOption effect (source order) and has
  // no reactive deps, so the zr handler is attached a single time — re-running
  // setOption with notMerge replaces the series but keeps the zrender instance.
  $effect(() => {
    chart = echarts.init(el);

    const onZrClick = (e) => {
      if (!onBucketClick) return;
      // Only count clicks inside the plotting grid (true even for whitespace
      // above a short bar), and map the pixel to an x-axis time value.
      if (!chart.containPixel("grid", [e.offsetX, e.offsetY])) return;
      const ms = chart.convertFromPixel({ xAxisIndex: 0 }, e.offsetX);
      if (ms != null) onBucketClick(ms);
    };
    chart.getZr().on("click", onZrClick);

    const ro = new ResizeObserver(() => chart?.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart?.dispose(); // also tears down the zr click handler
      chart = null;
    };
  });

  // Reactive on `option`: re-render whenever it changes. notMerge so a new option
  // fully replaces stale series (e.g. raw toggle off). Guarded on `chart` so it's
  // a no-op until the lifecycle effect above has initialized it.
  $effect(() => {
    if (chart && option) chart.setOption(option, { notMerge: true });
  });
</script>

<div class="chart-wrap" bind:this={el}></div>
