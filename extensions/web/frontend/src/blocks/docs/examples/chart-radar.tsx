import { PolarAngleAxis, PolarGrid, Radar, RadarChart } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";

const VISITS = [
  { month: "January", desktop: 186 },
  { month: "February", desktop: 305 },
  { month: "March", desktop: 237 },
  { month: "April", desktop: 273 },
  { month: "May", desktop: 209 },
  { month: "June", desktop: 214 },
];

const CONFIG = { desktop: { label: "Desktop", color: "var(--blk-primary)" } } satisfies ChartConfig;

export function ChartRadar() {
  return (
    <ChartContainer config={CONFIG} className="blk-chart-square">
      <RadarChart data={VISITS}>
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <PolarGrid />
        <PolarAngleAxis dataKey="month" tickFormatter={(month: string) => month.slice(0, 3)} />
        <Radar
          dataKey="desktop"
          fill="var(--blk-color-desktop)"
          fillOpacity={0.4}
          stroke="var(--blk-color-desktop)"
          strokeWidth={2}
        />
      </RadarChart>
    </ChartContainer>
  );
}
