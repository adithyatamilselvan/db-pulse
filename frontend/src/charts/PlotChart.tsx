import { lazy, Suspense, useMemo } from "react";
import type { Data, Layout } from "plotly.js";
import { useApp } from "../hooks/AppContext";
import { LoadingState } from "../components/ui";

// Plotly is large; load it only when a chart is first rendered.
const Plot = lazy(async () => {
  const [{ default: createPlotlyComponent }, plotly] = await Promise.all([
    import("react-plotly.js/factory"),
    import("plotly.js-basic-dist-min"),
  ]);
  return { default: createPlotlyComponent((plotly as { default?: unknown }).default ?? plotly) };
});

export const CHART_COLORS = ["#22d3ee", "#a78bfa", "#fbbf24", "#34d399", "#f87171", "#60a5fa"];

export function PlotChart({ data, layout, height = 300, testId }: { data: Data[]; layout?: Partial<Layout>; height?: number; testId?: string }) {
  const { theme } = useApp();
  const merged = useMemo<Partial<Layout>>(() => {
    const ink = theme === "dark" ? "#cbd5e1" : "#334155";
    const grid = theme === "dark" ? "#1e293b" : "#e2e8f0";
    return {
      autosize: true,
      height,
      margin: { l: 52, r: 16, t: 24, b: 40 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: ink, family: "Inter, system-ui, sans-serif", size: 12 },
      legend: { orientation: "h", y: -0.2 },
      colorway: CHART_COLORS,
      xaxis: { gridcolor: grid, zerolinecolor: grid },
      yaxis: { gridcolor: grid, zerolinecolor: grid },
      ...layout,
    };
  }, [theme, height, layout]);
  return (
    <div data-testid={testId} className="w-full">
      <Suspense fallback={<LoadingState label="Loading chart…" />}>
        <Plot data={data} layout={merged} config={{ displaylogo: false, responsive: true, displayModeBar: false }} useResizeHandler style={{ width: "100%" }} />
      </Suspense>
    </div>
  );
}
