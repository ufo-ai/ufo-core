import { Bar, BarChart, CartesianGrid, Rectangle, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const BROWSERS = [
  { browser: "chrome", visitors: 275 },
  { browser: "safari", visitors: 200 },
  { browser: "firefox", visitors: 187 },
  { browser: "edge", visitors: 173 },
  { browser: "other", visitors: 90 },
];

const ACTIVE = 2;

const CONFIG = {
  visitors: { label: "Visitors", color: "var(--blk-primary)" },
  chrome: { label: "Chrome" },
  safari: { label: "Safari" },
  firefox: { label: "Firefox" },
  edge: { label: "Edge" },
  other: { label: "Other" },
} satisfies ChartConfig;

export function ChartBarActive() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={BROWSERS} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="browser"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(browser: string) => String(CONFIG[browser as keyof typeof CONFIG].label)}
        />
        <ChartTooltip
          cursor={false}
          defaultIndex={ACTIVE}
          content={<ChartTooltipContent hideLabel nameKey="browser" />}
        />
        <Bar
          dataKey="visitors"
          fill="var(--blk-color-visitors)"
          radius={8}
          maxBarSize={56}
          activeBar={<Rectangle fillOpacity={0.65} stroke="var(--blk-color-visitors)" strokeWidth={2} />}
        />
      </BarChart>
    </ChartContainer>
  );
}
