import { Cell, Pie, PieChart } from "recharts";

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/blocks/chart";

const BROWSERS = [
  { browser: "chrome", visitors: 275 },
  { browser: "safari", visitors: 200 },
  { browser: "firefox", visitors: 187 },
  { browser: "edge", visitors: 173 },
  { browser: "other", visitors: 90 },
];

const CONFIG = {
  visitors: { label: "Visitors" },
  chrome: { label: "Chrome", color: "var(--blk-primary)" },
  safari: { label: "Safari", color: "var(--blk-secondary)" },
  firefox: { label: "Firefox", color: "color-mix(in srgb, var(--blk-text-1) 70%, transparent)" },
  edge: { label: "Edge", color: "color-mix(in srgb, var(--blk-text-1) 46%, transparent)" },
  other: { label: "Other", color: "color-mix(in srgb, var(--blk-text-1) 28%, transparent)" },
} satisfies ChartConfig;

export function ChartPieLegend() {
  return (
    <ChartContainer config={CONFIG} className="blk-chart-square">
      <PieChart>
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel nameKey="browser" />} />
        <Pie
          data={BROWSERS}
          dataKey="visitors"
          nameKey="browser"
          innerRadius={48}
          stroke="var(--blk-bg-100)"
          strokeWidth={2}
        >
          {BROWSERS.map((row) => (
            <Cell key={row.browser} fill={`var(--blk-color-${row.browser})`} />
          ))}
        </Pie>
        <ChartLegend content={<ChartLegendContent nameKey="browser" />} />
      </PieChart>
    </ChartContainer>
  );
}
