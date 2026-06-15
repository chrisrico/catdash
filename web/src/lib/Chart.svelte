<script>
  import echarts from "./echarts.js";

  // onBucketClick(ms) fires when the plot area is clicked, with the x-axis time
  // value (ms) under the cursor — the parent floors it to a bucket and drills in.
  let { option, onBucketClick } = $props();

  let el;
  let chart;

  // Lifecycle effect: create the chart, observe resizes, and wire the pointer
  // handler ONCE. It must run before the setOption effect (source order) and has
  // no reactive deps, so the zr handler is attached a single time — re-running
  // setOption with notMerge replaces the series but keeps the zrender instance.
  $effect(() => {
    chart = echarts.init(el);

    // Map a plot pixel to its x-axis time value and drill in. Only counts
    // points inside the plotting grid (true even for whitespace above a short
    // bar).
    const fire = (x, y) => {
      if (!onBucketClick) return;
      if (x == null || !chart.containPixel("grid", [x, y])) return;
      const ms = chart.convertFromPixel({ xAxisIndex: 0 }, x);
      if (ms == null) return;
      onBucketClick(ms);
      // On touch the axis-pointer tooltip ("value box") would linger on top of
      // the modal — a finger never "leaves" the chart. Opening the modal also
      // locks body scroll, which resizes the chart and revives the tooltip, so
      // hide it now and again after that resize settles.
      const hideTip = () => chart?.dispatchAction({ type: "hideTip" });
      hideTip();
      setTimeout(hideTip, 50);
    };

    const zr = chart.getZr();
    // Touch: tapping a thin bar is imprecise, but dragging moves the axis
    // pointer ("the selector") to a target time. So on a coarse pointer, open
    // the detail on drag-end using the last finger position (a plain tap moves
    // nothing, so it still opens at the tap spot). Mouse stays click-to-open.
    if (window.matchMedia?.("(pointer: coarse)").matches) {
      let x = null;
      let y = null;
      const track = (e) => {
        x = e.offsetX;
        y = e.offsetY;
      };
      zr.on("mousedown", track);
      zr.on("mousemove", track);
      zr.on("mouseup", () => fire(x, y));
    } else {
      zr.on("click", (e) => fire(e.offsetX, e.offsetY));
    }

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
