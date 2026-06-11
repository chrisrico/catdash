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

// Shared look that matches the dashboard's dark theme.
export const COLORS = {
  accent: "#4cc2ff",
  accent2: "#f0883e",
  good: "#57d977",
  muted: "#8b97a7",
  text: "#e6edf3",
  grid: "rgba(255,255,255,0.05)",
};

export const baseOption = {
  textStyle: { color: COLORS.text },
  legend: { textColor: COLORS.text, textStyle: { color: COLORS.text }, icon: "circle" },
  tooltip: {
    trigger: "axis",
    backgroundColor: "#1c232c",
    borderColor: "#2a323d",
    textStyle: { color: COLORS.text },
  },
  grid: { left: 56, right: 56, top: 48, bottom: 32 },
};

export const timeAxis = {
  type: "time",
  axisLine: { lineStyle: { color: COLORS.muted } },
  axisLabel: { color: COLORS.muted, hideOverlap: true },
  splitLine: { show: false },
};

export const valueAxis = {
  type: "value",
  axisLabel: { color: COLORS.muted },
  splitLine: { lineStyle: { color: COLORS.grid } },
};
