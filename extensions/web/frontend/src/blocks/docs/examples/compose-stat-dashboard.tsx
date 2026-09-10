import { Area, AreaChart, CartesianGrid, XAxis } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/blocks/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";
import { StatGrid, StatTile } from "@/blocks/stat";

const REPLIES = [
  { month: "April", handled: 186 },
  { month: "May", handled: 305 },
  { month: "June", handled: 237 },
  { month: "July", handled: 273 },
  { month: "August", handled: 209 },
  { month: "September", handled: 314 },
];

const CONFIG = { handled: { label: "Handled", color: "var(--blk-primary)" } } satisfies ChartConfig;

export default function StatDashboard() {
  return (
    <Card size="lg">
      <CardHeader>
        <CardTitle size="lg">Assistant load</CardTitle>
        <CardDescription>What the agent handled across the last six months.</CardDescription>
      </CardHeader>
      <CardContent>
        <StatGrid columns={3}>
          <StatTile label="Handled" value="1,524" delta={{ value: "12%", direction: "up" }} />
          <StatTile label="Median reply" value="1.8s" delta={{ value: "0.3s", direction: "down" }} />
          <StatTile label="Escalated" value="34" sub="2.2% of turns" />
        </StatGrid>
        <ChartContainer config={CONFIG}>
          <AreaChart data={REPLIES} margin={{ left: 12, right: 12 }}>
            <defs>
              <linearGradient id="blk-fill-handled" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="var(--blk-color-handled)" stopOpacity={0.8} />
                <stop offset="95%" stopColor="var(--blk-color-handled)" stopOpacity={0.05} />
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
              dataKey="handled"
              type="natural"
              fill="url(#blk-fill-handled)"
              stroke="var(--blk-color-handled)"
            />
          </AreaChart>
        </ChartContainer>
      </CardContent>
    </Card>
  );
}
