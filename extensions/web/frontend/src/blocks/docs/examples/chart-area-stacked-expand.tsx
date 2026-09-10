import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186, mobile: 80, other: 45 },
  { month: "February", desktop: 305, mobile: 200, other: 100 },
  { month: "March", desktop: 237, mobile: 120, other: 150 },
  { month: "April", desktop: 73, mobile: 190, other: 50 },
  { month: "May", desktop: 209, mobile: 130, other: 100 },
  { month: "June", desktop: 214, mobile: 140, other: 160 },
];

const CONFIG = {
  desktop: { label: "Desktop", color: "var(--blk-primary)" },
  mobile: { label: "Mobile", color: "var(--blk-secondary)" },
  other: { label: "Other", color: "color-mix(in srgb, var(--blk-text-1) 70%, transparent)" },
} satisfies ChartConfig;

export function ChartAreaStackedExpand() {
  return (
    <ChartContainer config={CONFIG}>
      <AreaChart
        accessibilityLayer
        data={VISITS}
        stackOffset="expand"
        margin={{ left: 12, right: 12, top: 12 }}
      >
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="line" />} />
        <Area
          dataKey="other"
          type="natural"
          stackId="visits"
          fill="var(--blk-color-other)"
          fillOpacity={0.2}
          stroke="var(--blk-color-other)"
        />
        <Area
          dataKey="mobile"
          type="natural"
          stackId="visits"
          fill="var(--blk-color-mobile)"
          fillOpacity={0.4}
          stroke="var(--blk-color-mobile)"
        />
        <Area
          dataKey="desktop"
          type="natural"
          stackId="visits"
          fill="var(--blk-color-desktop)"
          fillOpacity={0.4}
          stroke="var(--blk-color-desktop)"
        />
      </AreaChart>
    </ChartContainer>
  );
}
