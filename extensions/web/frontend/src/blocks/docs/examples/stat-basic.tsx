import { Stat } from "@/blocks/stat";

export function StatBasic() {
  return (
    <div style={{ display: "flex", gap: 48, alignItems: "flex-start" }}>
      <Stat label="Revenue" value="$42,800" delta={{ value: "12.4%", direction: "up" }} />
      <Stat label="Churn" value="2.1%" delta={{ value: "0.3%", direction: "down" }} />
      <Stat
        label="Contracted"
        value="$273,000"
        size="lg"
        delta={{ value: "No change", direction: "flat" }}
      />
    </div>
  );
}
