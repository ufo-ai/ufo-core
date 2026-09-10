import { IconHash } from "@tabler/icons-react";

import { Item, ItemActions, ItemContent, ItemGroup, ItemMedia, ItemMeta, ItemTitle } from "@/blocks/item";

const CHANNELS = [
  { name: "design", count: "12" },
  { name: "engineering", count: "4" },
  { name: "revenue", count: "1" },
];

export function ItemExtraSmall() {
  return (
    <ItemGroup flush>
      {CHANNELS.map((channel) => (
        <Item key={channel.name} size="xs">
          <ItemMedia variant="icon">
            <IconHash size={16} stroke={1.5} />
          </ItemMedia>
          <ItemContent>
            <ItemTitle>{channel.name}</ItemTitle>
          </ItemContent>
          <ItemActions>
            <ItemMeta>{channel.count}</ItemMeta>
          </ItemActions>
        </Item>
      ))}
    </ItemGroup>
  );
}
