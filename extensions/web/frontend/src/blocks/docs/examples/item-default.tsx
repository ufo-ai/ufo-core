import { IconCalendarEvent, IconUsers } from "@tabler/icons-react";

import { Count, Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemMeta, ItemTitle } from "@/blocks/item";

export function ItemDefault() {
  return (
    <Item>
      <ItemMedia variant="icon">
        <IconCalendarEvent size={16} stroke={1.5} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Design sync</ItemTitle>
        <ItemDescription>Agree the navigation pattern and the next steps for the week.</ItemDescription>
        <ItemMeta>2:30p • Google Meets</ItemMeta>
      </ItemContent>
      <ItemActions>
        <Count icon={<IconUsers size={14} stroke={1.5} />}>8</Count>
      </ItemActions>
    </Item>
  );
}
