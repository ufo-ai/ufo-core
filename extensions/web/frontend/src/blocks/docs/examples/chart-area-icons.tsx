import { IconDeviceDesktop, IconDeviceMobile } from "@tabler/icons-react";
import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

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
  desktop: { label: "Desktop", color: "var(--blk-primary)", icon: IconDeviceDesktop },
  mobile: { label: "Mobile", color: "var(--blk-secondary)", icon: IconDeviceMobile },
} satisfies ChartConfig;

export function ChartAreaIcons() {
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
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
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
        <ChartLegend content={<ChartLegendContent />} />
      </AreaChart>
    </ChartContainer>
  );
}
