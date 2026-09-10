import { useState } from "react";
import { IconDots, IconFileText } from "@tabler/icons-react";

import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/blocks/item";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/blocks/menu";

export function ItemDropdown() {
  const [act, setAct] = useState("No act taken yet.");
  return (
    <Item variant="outline">
      <ItemMedia variant="icon">
        <IconFileText size={16} stroke={1.5} />
      </ItemMedia>
      <ItemContent>
        <ItemTitle>Migration plan</ItemTitle>
        <ItemDescription>{act}</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Menu>
          <MenuTrigger className="blk-menu-trigger" data-icon="true" aria-label="More">
            <IconDots size={16} stroke={1.5} />
          </MenuTrigger>
          <MenuContent>
            <MenuItem onSelect={() => setAct("Opened.")}>Open</MenuItem>
            <MenuItem onSelect={() => setAct("Renamed.")}>Rename</MenuItem>
            <MenuItem destructive onSelect={() => setAct("Deleted.")}>
              Delete
            </MenuItem>
          </MenuContent>
        </Menu>
      </ItemActions>
    </Item>
  );
}
