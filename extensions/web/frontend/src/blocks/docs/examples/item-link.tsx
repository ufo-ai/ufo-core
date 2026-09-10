import { IconChevronRight, IconCards } from "@tabler/icons-react";

import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/blocks/item";

export function ItemLink() {
  return (
    <Item variant="outline" render={<a href="#/card" />}>
      <ItemMedia variant="icon">
        <IconCards size={16} stroke={1.5} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Card</ItemTitle>
        <ItemDescription>A bordered surface with header, content and footer slots.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <IconChevronRight size={16} stroke={1.5} />
      </ItemActions>
    </Item>
  );
}
