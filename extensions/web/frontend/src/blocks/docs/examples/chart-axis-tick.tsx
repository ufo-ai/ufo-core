import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartTick,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from "@/blocks/chart";

const NIGHTS = [
  { night: "sep2", rate: 72.5, scored: "1101/1518" },
  { night: "sep3", rate: 73.7, scored: "1131/1534" },
  { night: "sep4", rate: 73.3, scored: "1132/1544" },
  { night: "sep5", rate: 73.4, scored: "1132/1543" },
  { night: "sep6", rate: 69.6, scored: "905/1300" },
  { night: "sep7", rate: 71.9, scored: "1187/1652" },
  { night: "sep8", rate: 71.5, scored: "1192/1666" },
];

const SCORED = new Map(NIGHTS.map((night) => [night.night, night.scored]));

const CONFIG = { rate: { label: "Pass rate", color: "var(--blk-primary)" } } satisfies ChartConfig;

export function ChartAxisTick() {
  return (
    <ChartContainer config={CONFIG}>
      <LineChart accessibilityLayer data={NIGHTS} margin={{ left: 12, right: 12 }}>
        <CartesianGrid vertical={false} />
        <YAxis
          dataKey="rate"
          domain={[68, 76]}
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          width={40}
          tickFormatter={(rate: number) => `${rate}%`}
        />
        <XAxis
          dataKey="night"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          height={44}
          tick={(props) => <ChartTick {...props} lines={(night) => [night, SCORED.get(night)]} />}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
        <Line
          dataKey="rate"
          type="linear"
          strokeWidth={2}
          stroke="var(--blk-color-rate)"
          dot={{ fill: "var(--blk-color-rate)", r: 3 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ChartContainer>
  );
}
