import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186, mobile: 80 },
  { month: "February", desktop: 305, mobile: 200 },
  { month: "March", desktop: 237, mobile: 120 },
  { month: "April", desktop: 73, mobile: 190 },
  { month: "May", desktop: 209, mobile: 130 },
  { month: "June", desktop: 214, mobile: 140 },
];

const CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
} satisfies ChartConfig;

export function ChartArea() {
  return (
    <ChartContainer config={CONFIG}>
      <AreaChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
        <defs>
          <linearGradient id="blk-area-desktop" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--blk-color-desktop)" stopOpacity={0.8} />
            <stop offset="95%" stopColor="var(--blk-color-desktop)" stopOpacity={0.05} />
          </linearGradient>
          <linearGradient id="blk-area-mobile" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--blk-color-mobile)" stopOpacity={0.8} />
            <stop offset="95%" stopColor="var(--blk-color-mobile)" stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Area
          dataKey="mobile"
          type="natural"
          stackId="visits"
          fill="url(#blk-area-mobile)"
          stroke="var(--blk-color-mobile)"
        />
        <Area
          dataKey="desktop"
          type="natural"
          stackId="visits"
          fill="url(#blk-area-desktop)"
          stroke="var(--blk-color-desktop)"
        />
      </AreaChart>
    </ChartContainer>
  );
}
