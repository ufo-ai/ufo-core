import { useState } from "react";
import { IconCalendar, IconDots } from "@tabler/icons-react";

import { ActionBar, ActionBarActions, ActionBarTitle, IconButton } from "@/blocks/action-bar";
import { MenuCheckboxItem, MenuContent, MenuLabel } from "@/blocks/menu";

const LANES = ["Todos", "Meetings", "Leads"];

export function ActionBarTitleMenu() {
  const [lane, setLane] = useState(LANES[0]);
  return (
    <ActionBar>
      <ActionBarTitle
        icon={<IconCalendar size={16} stroke={1.5} />}
        menu={
          <MenuContent align="start">
            <MenuLabel>Lane</MenuLabel>
            {LANES.map((name) => (
              <MenuCheckboxItem
                key={name}
                checked={name === lane}
                onCheckedChange={() => setLane(name)}
              >
                {name}
              </MenuCheckboxItem>
            ))}
          </MenuContent>
        }
      >
        {lane}
      </ActionBarTitle>
      <ActionBarActions>
        <IconButton label="More">
          <IconDots size={16} stroke={1.5} />
        </IconButton>
      </ActionBarActions>
    </ActionBar>
  );
}
