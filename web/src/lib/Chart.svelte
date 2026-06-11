<script>
  import echarts from "./echarts.js";

  let { option } = $props();

  let el;
  let chart;

  $effect(() => {
    if (!chart) chart = echarts.init(el);
    // notMerge so a re-render fully replaces stale series (e.g. raw toggle off).
    if (option) chart.setOption(option, { notMerge: true });
  });

  $effect(() => {
    const ro = new ResizeObserver(() => chart?.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart?.dispose();
      chart = null;
    };
  });
</script>

<div class="chart-wrap" bind:this={el}></div>
