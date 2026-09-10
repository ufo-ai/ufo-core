import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186 },
  { month: "February", desktop: 305 },
  { month: "March", desktop: 237 },
  { month: "April", desktop: 73 },
  { month: "May", desktop: 209 },
  { month: "June", desktop: 214 },
];

const CONFIG = { desktop: { label: "Desktop", color: "var(--blk-primary)" } } satisfies ChartConfig;

export function ChartAreaStep() {
  return (
    <ChartContainer config={CONFIG}>
      <AreaChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <Area
          dataKey="desktop"
          type="step"
          fill="var(--blk-color-desktop)"
          fillOpacity={0.4}
          stroke="var(--blk-color-desktop)"
        />
      </AreaChart>
    </ChartContainer>
  );
}
