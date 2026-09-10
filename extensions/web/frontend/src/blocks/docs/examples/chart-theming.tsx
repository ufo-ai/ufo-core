import { Bar, BarChart, CartesianGrid, XAxis } from "recharts";

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
  { month: "April", desktop: 173, mobile: 190 },
  { month: "May", desktop: 209, mobile: 130 },
  { month: "June", desktop: 214, mobile: 140 },
];

const CONFIG = {
  desktop: { label: "Desktop", theme: { light: "#0095ff", dark: "#6cc4ff" } },
  mobile: { label: "Mobile", theme: { light: "#ff6700", dark: "#ffa057" } },
} satisfies ChartConfig;

export function ChartTheming() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={VISITS} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="month"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(month: string) => month.slice(0, 3)}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Bar dataKey="desktop" fill="var(--blk-color-desktop)" radius={8} maxBarSize={28} />
        <Bar dataKey="mobile" fill="var(--blk-color-mobile)" radius={8} maxBarSize={28} />
        <ChartLegend content={<ChartLegendContent />} />
      </BarChart>
    </ChartContainer>
  );
}
