import { Stat } from "@/blocks/stat";

export function StatMono() {
  return (
    <div style={{ display: "flex", gap: 48, alignItems: "flex-start", maxWidth: 640 }}>
      <Stat
        font="mono"
        casing="sentence"
        label="Net cash flow"
        value="+$20.6k"
        delta={{ value: "$80.4k", direction: "up" }}
        sub="Counted as the cash-basis result for the week: a surplus, because a $60,000 invoice was paid and treasury paid $92,492.84 of dividends. The week before re-reads at $60,767.07 burn."
      />
      <Stat
        font="mono"
        casing="sentence"
        label="Monthly recurring revenue"
        value="$0"
        delta={{ value: "flat", direction: "flat" }}
        sub="Counted as active subscriptions priced by the month, the only place revenue lives."
      />
    </div>
  );
}
