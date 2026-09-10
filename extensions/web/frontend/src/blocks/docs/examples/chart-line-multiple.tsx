import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/blocks/chart";

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

export function ChartLineMultiple() {
  return (
    <ChartContainer config={CONFIG}>
      <LineChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <YAxis tickLine={false} axisLine={false} tickMargin={8} width={36} />
        <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="line" />} />
        <Line dataKey="desktop" type="monotone" dot={false} strokeWidth={2} stroke="var(--blk-color-desktop)" />
        <Line dataKey="mobile" type="monotone" dot={false} strokeWidth={2} stroke="var(--blk-color-mobile)" />
        <ChartLegend content={<ChartLegendContent />} />
      </LineChart>
    </ChartContainer>
  );
}
