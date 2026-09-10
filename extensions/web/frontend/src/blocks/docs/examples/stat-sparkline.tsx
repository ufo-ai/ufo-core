import { Sparkline } from "@/blocks/stat";

export function StatSparkline() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span>Deploys per day</span>
        <Sparkline values={[4, 9, 6, 12]} kind="bars" />
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span>Response time</span>
        <Sparkline values={[6, 14, 9, 22, 12, 19, 10]} kind="line" />
      </div>
    </div>
  );
}
