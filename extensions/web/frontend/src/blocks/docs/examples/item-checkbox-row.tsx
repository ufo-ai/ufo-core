import { useState } from "react";

import { Checkbox } from "@/blocks/checkbox";
import { Item, ItemContent, ItemGroup, ItemMedia, ItemMeta, ItemTitle } from "@/blocks/item";

const ROWS = [
  { id: "plan", title: "Draft the migration plan" },
  { id: "audit", title: "Review the connector audit" },
  { id: "copy", title: "Rewrite the onboarding copy" },
];

export function ItemCheckboxRow() {
  const [done, setDone] = useState<string[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <ItemGroup>
      {ROWS.map((row) => (
        <Item key={row.id} size="sm" state={open === row.id ? "active" : "default"} onClick={() => setOpen(row.id)}>
          <ItemMedia variant="checkbox">
            <Checkbox
              label={`Finish ${row.title}`}
              checked={done.includes(row.id)}
              onCheckedChange={(on) =>
                setDone(on ? [...done, row.id] : done.filter((id) => id !== row.id))
              }
            />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{row.title}</ItemTitle>
          </ItemContent>
          {open === row.id ? <ItemMeta>Open</ItemMeta> : null}
        </Item>
      ))}
    </ItemGroup>
  );
}
