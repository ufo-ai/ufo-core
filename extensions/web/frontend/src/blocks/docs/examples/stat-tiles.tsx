import { StatGrid, StatTile } from "@/blocks/stat";

export function StatTiles() {
  return (
    <StatGrid columns={2}>
      <StatTile size="sm" label="Upcoming" value="$12,400" sub="Due 14 October" />
      <StatTile size="sm" label="Savings Plan" value="$8,150" sub="Paid monthly" />
    </StatGrid>
  );
}
