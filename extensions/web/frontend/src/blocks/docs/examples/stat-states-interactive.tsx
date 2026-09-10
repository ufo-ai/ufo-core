import { useState } from "react";

import { Prompt, Prompts } from "@/blocks/action-bar";
import { StatGrid, StatTile } from "@/blocks/stat";

const WIDTHS = [880, 640, 320];
const TILES = [
  { label: "Runs", value: "1,284" },
  { label: "Passed", value: "1,197" },
  { label: "Failed", value: "87" },
  { label: "Median", value: "2.4s" },
];

export function StatStatesInteractive() {
  const [width, setWidth] = useState(880);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, overflowX: "auto" }}>
      <Prompts wrap>
        {WIDTHS.map((step) => (
          <Prompt key={step} active={width === step} onClick={() => setWidth(step)}>
            {`${step}px`}
          </Prompt>
        ))}
      </Prompts>
      <div style={{ flex: "none", width }}>
        <StatGrid columns={4}>
          {TILES.map((tile) => (
            <StatTile key={tile.label} size="sm" label={tile.label} value={tile.value} />
          ))}
        </StatGrid>
      </div>
    </div>
  );
}
