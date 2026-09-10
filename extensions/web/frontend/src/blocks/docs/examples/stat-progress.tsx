import { ProgressStat, StatGrid } from "@/blocks/stat";

export function StatProgress() {
  return (
    <StatGrid columns={2}>
      <ProgressStat label="Annual target" value="$177,450" percent={65} target="$273,000" />
      <ProgressStat label="New accounts" value="64" percent={32} target="200" />
    </StatGrid>
  );
}
