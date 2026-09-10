import { Stat } from "@/blocks/stat";

export function StatSentence() {
  return (
    <div style={{ display: "flex", gap: 48, alignItems: "flex-start" }}>
      <Stat
        casing="sentence"
        label="Median time to merge"
        value="57m"
        delta={{ value: "1m", direction: "down", tone: "neutral" }}
        sub="Counted from the first commit pushed to the merge, drafts included."
      />
      <Stat label="Median time to merge" value="57m" sub="The same measure with the default label." />
    </div>
  );
}
