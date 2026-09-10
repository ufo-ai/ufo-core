import { CartesianGrid, LabelList, Line, LineChart, XAxis } from "recharts";

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

export function ChartLineLabel() {
  return (
    <ChartContainer config={CONFIG}>
      <LineChart accessibilityLayer data={VISITS} margin={{ left: 16, right: 16, top: 24 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="line" />} />
        <Line
          dataKey="desktop"
          type="natural"
          strokeWidth={2}
          stroke="var(--blk-color-desktop)"
          dot={{ fill: "var(--blk-color-desktop)", r: 4 }}
          activeDot={{ r: 6 }}
        >
          <LabelList dataKey="desktop" position="top" offset={12} className="blk-chart-label-muted" />
        </Line>
      </LineChart>
    </ChartContainer>
  );
}
