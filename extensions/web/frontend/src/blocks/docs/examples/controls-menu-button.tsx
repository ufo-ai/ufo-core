import { useState } from "react";
import { IconDots } from "@tabler/icons-react";

import { Menu, MenuButton, MenuCheckboxItem, MenuContent, MenuItem, MenuLabel } from "@/blocks/menu";

const VIEWS = ["Board", "List", "Calendar"];

export function ControlsMenuButton() {
  const [view, setView] = useState(VIEWS[0]);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <Menu>
        <MenuButton>{view}</MenuButton>
        <MenuContent align="start">
          <MenuLabel>View</MenuLabel>
          {VIEWS.map((name) => (
            <MenuCheckboxItem
              key={name}
              checked={name === view}
              onCheckedChange={() => setView(name)}
            >
              {name}
            </MenuCheckboxItem>
          ))}
        </MenuContent>
      </Menu>
      <Menu>
        <MenuButton icon label="More">
          <IconDots size={16} stroke={1.5} />
        </MenuButton>
        <MenuContent>
          <MenuItem>Rename</MenuItem>
          <MenuItem destructive>Delete</MenuItem>
        </MenuContent>
      </Menu>
    </div>
  );
}
