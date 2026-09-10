import { useState } from "react";
import { IconChevronDown } from "@tabler/icons-react";

import {
  Menu,
  MenuCheckboxItem,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
} from "@/blocks/menu";

export function ControlsMenu() {
  const [past, setPast] = useState(true);
  const [declined, setDeclined] = useState(false);
  return (
    <div>
      <Menu>
        <MenuTrigger className="blk-menu-trigger">
          View
          <IconChevronDown size={16} stroke={1.5} />
        </MenuTrigger>
        <MenuContent align="start">
          <MenuLabel>Show</MenuLabel>
          <MenuCheckboxItem checked={past} onCheckedChange={setPast}>
            Past meetings
          </MenuCheckboxItem>
          <MenuCheckboxItem checked={declined} onCheckedChange={setDeclined}>
            Declined meetings
          </MenuCheckboxItem>
          <MenuSeparator />
          <MenuItem>Rename</MenuItem>
          <MenuItem disabled>Duplicate</MenuItem>
          <MenuItem destructive>Delete</MenuItem>
        </MenuContent>
      </Menu>
    </div>
  );
}
