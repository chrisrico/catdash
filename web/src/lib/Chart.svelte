<script>
  import echarts from "./echarts.js";

  // onBucketClick(ms) fires when the plot area is acted on, with the x-axis time
  // value (ms) under the pointer — the parent snaps it to the nearest bucket and
  // drills in.
  let { option, onBucketClick } = $props();

  let el;
  let chart;

  // Lifecycle effect: create the chart, observe resizes, and wire the pointer
  // handler ONCE. It must run before the setOption effect (source order) and has
  // no reactive deps, so the zr handler is attached a single time — re-running
  // setOption with notMerge replaces the series but keeps the zrender instance.
  $effect(() => {
    chart = echarts.init(el);

    // Map a plot pixel to its x-axis time value and drill in. Only counts points
    // inside the plotting grid (true even for whitespace above a short bar). The
    // parent rounds the value to the nearest bucket — bars are centered on their
    // bucket start, so flooring a raw pixel lands a bucket early in a bar's left
    // half, which at a few-pixel-wide daily bar is well within a fingertip.
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
    // pointer ("the selector") to a target bucket. So on a coarse pointer, open
    // the detail on drag-end using the last finger position. Mouse stays
    // click-to-open.
    //
    // We read the finger position from the *native* touch events on the canvas,
    // not from zrender's mousedown/move/up. zrender reports e.offsetX relative
    // to whatever display element is under the pointer (a bar), so on drag-end
    // over an empty day — or over a day whose neighbor has a bar — the offset
    // snaps back to the originating bar's column and the wrong (or empty) bucket
    // opens. clientX minus the canvas rect is always canvas-relative regardless
    // of what's painted underneath, so empty days convert correctly.
    let onTouchEnd = null;
    if (window.matchMedia?.("(pointer: coarse)").matches) {
      let x = null;
      let y = null;
      const track = (e) => {
        const t = e.touches?.[0] ?? e.changedTouches?.[0];
        if (!t) return;
        const r = el.getBoundingClientRect();
        x = t.clientX - r.left;
        y = t.clientY - r.top;
      };
      onTouchEnd = (e) => {
        track(e);
        fire(x, y);
      };
      el.addEventListener("touchstart", track, { passive: true });
      el.addEventListener("touchmove", track, { passive: true });
      el.addEventListener("touchend", onTouchEnd, { passive: true });
      // store track so cleanup can remove the move/start listeners too
      el._cdTrack = track;
    } else {
      zr.on("click", (e) => fire(e.offsetX, e.offsetY));
    }

    const ro = new ResizeObserver(() => chart?.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (el._cdTrack) {
        el.removeEventListener("touchstart", el._cdTrack);
        el.removeEventListener("touchmove", el._cdTrack);
        el.removeEventListener("touchend", onTouchEnd);
        delete el._cdTrack;
      }
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
