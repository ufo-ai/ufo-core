import { useState } from "react";

import { Item, ItemActions, ItemContent, ItemGroup, ItemTitle, Tag } from "@/blocks/item";

const ROWS = [
  { id: "plan", title: "Draft the migration plan", tag: "Platform" },
  { id: "audit", title: "Review the connector audit", tag: "Security" },
  { id: "copy", title: "Rewrite the onboarding copy", tag: "Platform" },
];

export function ItemTagToggle() {
  const [held, setHeld] = useState<string | null>(null);
  return (
    <ItemGroup>
      {ROWS.map((row) => (
        <Item key={row.id} size="sm">
          <ItemContent>
            <ItemTitle>{row.title}</ItemTitle>
          </ItemContent>
          <ItemActions>
            <Tag pressed={held === row.tag} onClick={() => setHeld(held === row.tag ? null : row.tag)}>
              {row.tag}
            </Tag>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  );
}
