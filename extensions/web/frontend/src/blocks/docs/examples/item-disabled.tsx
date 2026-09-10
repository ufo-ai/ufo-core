import { IconLock } from "@tabler/icons-react";

import { Item, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/blocks/item";

export function ItemDisabled() {
  return (
    <Item state="disabled" onClick={() => undefined}>
      <ItemMedia variant="icon">
        <IconLock size={16} stroke={1.5} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Billing</ItemTitle>
        <ItemDescription>An owner grants access to this record.</ItemDescription>
      </ItemContent>
    </Item>
  );
}
