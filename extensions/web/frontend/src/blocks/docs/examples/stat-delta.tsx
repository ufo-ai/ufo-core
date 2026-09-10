import { Delta } from "@/blocks/stat";

export function StatDeltaTones() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 13 }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        Shipped 298 <Delta value="56" direction="down" tone="neutral" />
      </span>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        Reverted 7 <Delta value="6" direction="up" tone="negative" />
      </span>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        Churn 2.1% <Delta value="0.3%" direction="down" tone="positive" />
      </span>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        Pass rate 71.5% <Delta value="0.4 pts" direction="down" />
      </span>
    </div>
  );
}
