import { Bar, BarChart, Cell, XAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/blocks/chart";
import { StatGrid, StatTile } from "@/blocks/stat";

const DIVIDENDS = [
  { month: "Jan", paid: 1840 },
  { month: "Feb", paid: 2960 },
  { month: "Mar", paid: 2210 },
  { month: "Apr", paid: 1520 },
  { month: "May", paid: 2740 },
];

const CONFIG = { paid: { label: "Paid" } } satisfies ChartConfig;

const PEAK = Math.max(...DIVIDENDS.map((row) => row.paid));
const FLOOR = 0.3;

export function StatBarReport() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      <ChartContainer config={CONFIG}>
        <BarChart data={DIVIDENDS} barCategoryGap={16} maxBarSize={64} margin={{ left: 12, right: 12 }}>
          <XAxis dataKey="month" tickLine={false} axisLine={false} tickMargin={8} />
          <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
          <Bar dataKey="paid" radius={8}>
            {DIVIDENDS.map((row) => (
              <Cell
                key={row.month}
                fill="var(--blk-text-1)"
                fillOpacity={FLOOR + (1 - FLOOR) * (row.paid / PEAK)}
              />
            ))}
          </Bar>
        </BarChart>
      </ChartContainer>
      <StatGrid columns={2}>
        <StatTile size="sm" label="Paid this year" value="$11,270" sub="Across 14 holdings" />
        <StatTile size="sm" label="Next payment" value="$1,940" sub="Due 3 June" />
      </StatGrid>
      <button type="button" className="blk-stat-cta">
        View full report
      </button>
    </div>
  );
}
