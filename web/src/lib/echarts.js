// Modular ECharts build: register only what the three dashboard charts use,
// keeping the self-hosted bundle small.
import * as echarts from "echarts/core";
import { LineChart, BarChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart,
  BarChart,
  ScatterChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);

export default echarts;

// Per-theme palettes. Charts call palette(themeState.resolved) and rebuild their
// options whenever the resolved theme changes, so canvases re-theme live.
export const PALETTES = {
  dark: {
    accent: "#4cc2ff",
    accent2: "#f0883e",
    good: "#57d977",
    muted: "#8b97a7",
    text: "#e6edf3",
    grid: "rgba(255,255,255,0.05)",
    tooltipBg: "#1c232c",
    tooltipBorder: "#2a323d",
    barFood: "rgba(240,136,62,0.6)",
    barCycle: "rgba(76,194,255,0.55)",
    rawDot: "rgba(139,151,167,0.4)",
  },
  light: {
    accent: "#0969da",
    accent2: "#bc4c00",
    good: "#1a7f37",
    muted: "#57606a",
    text: "#1f2328",
    grid: "rgba(27,31,36,0.08)",
    tooltipBg: "#ffffff",
    tooltipBorder: "#d0d7de",
    barFood: "rgba(191,76,0,0.5)",
    barCycle: "rgba(9,105,218,0.45)",
    rawDot: "rgba(87,96,106,0.35)",
  },
};

export const palette = (theme) => PALETTES[theme] ?? PALETTES.dark;

export const baseOption = (c) => ({
  textStyle: { color: c.text },
  legend: { textStyle: { color: c.text }, icon: "circle" },
  tooltip: {
    trigger: "axis",
    backgroundColor: c.tooltipBg,
    borderColor: c.tooltipBorder,
    textStyle: { color: c.text },
  },
  grid: { left: 56, right: 56, top: 48, bottom: 32 },
});

export const timeAxis = (c) => ({
  type: "time",
  axisLine: { lineStyle: { color: c.muted } },
  axisLabel: { color: c.muted, hideOverlap: true },
  splitLine: { show: false },
});

export const valueAxis = (c) => ({
  type: "value",
  axisLabel: { color: c.muted },
  splitLine: { lineStyle: { color: c.grid } },
});
