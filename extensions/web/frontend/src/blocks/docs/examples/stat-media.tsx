import { IconBrandGithub } from "@tabler/icons-react";

import { Tag } from "@/blocks/item";
import { Stat } from "@/blocks/stat";

export function StatMedia() {
  return (
    <div style={{ display: "flex", gap: 48, alignItems: "flex-start" }}>
      <Stat
        media={<IconBrandGithub size={16} stroke={1.5} />}
        label="Shipped"
        value="298"
        delta={{ value: "56", direction: "down", tone: "neutral" }}
      />
      <Stat
        media={<IconBrandGithub size={16} stroke={1.5} />}
        label={
          <>
            Reverted <Tag>main</Tag>
          </>
        }
        value="7"
        delta={{ value: "6", direction: "up", tone: "negative" }}
      />
    </div>
  );
}
