import { Bar, BarChart, Cell, XAxis, YAxis } from "recharts";

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

export function ChartBarMixed() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={BROWSERS} layout="vertical" margin={{ left: 8, right: 12 }}>
        <YAxis
          dataKey="browser"
          type="category"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={60}
          tickFormatter={(browser: string) => String(CONFIG[browser as keyof typeof CONFIG].label)}
        />
        <XAxis dataKey="visitors" type="number" hide />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel nameKey="browser" />} />
        <Bar dataKey="visitors" radius={8} maxBarSize={28}>
          {BROWSERS.map((row) => (
            <Cell key={row.browser} fill={`var(--blk-color-${row.browser})`} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
