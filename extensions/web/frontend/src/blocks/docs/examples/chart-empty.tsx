import { ChartContainer } from "@/blocks/chart";

export function ChartEmpty() {
  return (
    <ChartContainer config={{}}>
      <div className="blk-chart-empty">No data yet</div>
    </ChartContainer>
  );
}
