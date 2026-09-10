import { Bar, BarChart, CartesianGrid, LabelList, XAxis } from "recharts";

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

export function ChartBarLabel() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12, top: 20 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <Bar dataKey="desktop" fill="var(--blk-color-desktop)" radius={8} maxBarSize={56}>
          <LabelList dataKey="desktop" position="top" offset={8} className="blk-chart-label-value" />
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
