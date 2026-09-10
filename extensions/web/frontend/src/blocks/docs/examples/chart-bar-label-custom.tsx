import { Bar, BarChart, CartesianGrid, LabelList, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186 },
  { month: "February", desktop: 305 },
  { month: "March", desktop: 237 },
  { month: "April", desktop: 173 },
  { month: "May", desktop: 209 },
  { month: "June", desktop: 214 },
];

const CONFIG = { desktop: { label: "Desktop", color: "var(--blk-primary)" } } satisfies ChartConfig;

export function ChartBarLabelCustom() {
  return (
    <ChartContainer config={CONFIG}>
      <BarChart accessibilityLayer data={VISITS} layout="vertical" margin={{ right: 32 }}>
        <CartesianGrid horizontal={false} />
        <YAxis dataKey="month" type="category" hide />
        <XAxis dataKey="desktop" type="number" hide />
        <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="line" />} />
        <Bar dataKey="desktop" fill="var(--blk-color-desktop)" radius={8} maxBarSize={28}>
          <LabelList
            dataKey="month"
            position="insideLeft"
            offset={12}
            className="blk-chart-label-inverse"
          />
          <LabelList
            dataKey="desktop"
            position="right"
            offset={12}
            className="blk-chart-label-value"
          />
        </Bar>
      </BarChart>
    </ChartContainer>
  );
}
