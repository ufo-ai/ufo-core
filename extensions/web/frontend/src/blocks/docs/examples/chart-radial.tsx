import { Cell, RadialBar, RadialBarChart } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

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

export function ChartRadial() {
  return (
    <ChartContainer config={CONFIG} className="blk-chart-square">
      <RadialBarChart data={BROWSERS} innerRadius={32} outerRadius={116}>
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel nameKey="browser" />} />
        <RadialBar dataKey="visitors" background cornerRadius={8}>
          {BROWSERS.map((row) => (
            <Cell key={row.browser} fill={`var(--blk-color-${row.browser})`} />
          ))}
        </RadialBar>
      </RadialBarChart>
    </ChartContainer>
  );
}
