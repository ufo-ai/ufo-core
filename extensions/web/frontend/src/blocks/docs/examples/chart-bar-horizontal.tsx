import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts";

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

export function ChartBarHorizontal() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={VISITS} layout="vertical" margin={{ left: 8, right: 12 }}>
        <CartesianGrid horizontal={false} />
        <YAxis
          dataKey="month"
          type="category"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={44}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <XAxis dataKey="desktop" type="number" hide />
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel />} />
        <Bar dataKey="desktop" fill="var(--blk-color-desktop)" radius={8} maxBarSize={28} />
      </BarChart>
    </ChartContainer>
  );
}
