import { useState } from "react";

import { Item, ItemContent, ItemGroup, ItemHeader, ItemTitle } from "@/blocks/item";

const RECORDS = ["Draft the migration plan", "Review the connector audit"];

export function ItemCollapsible() {
  const [open, setOpen] = useState(true);
  return (
    <ItemGroup>
      <ItemHeader badge="26" accent="primary" collapsible open={open} onOpenChange={setOpen}>
        Today
      </ItemHeader>
      {open
        ? RECORDS.map((record) => (
            <Item key={record} size="sm">
              <ItemContent>
                <ItemTitle>{record}</ItemTitle>
              </ItemContent>
            </Item>
          ))
        : null}
    </ItemGroup>
  );
}
