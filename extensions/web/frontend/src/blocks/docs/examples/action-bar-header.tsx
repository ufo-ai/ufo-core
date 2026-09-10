import { IconCalendar, IconDots, IconPlus, IconSearch } from "@tabler/icons-react";

import { ActionBar, ActionBarActions, ActionBarTitle, IconButton } from "@/blocks/action-bar";

export function HeaderExample() {
  return (
    <ActionBar>
      <ActionBarTitle icon={<IconCalendar size={16} stroke={1.5} />} menu>
        Todos
      </ActionBarTitle>
      <ActionBarActions>
        <IconButton label="New todo">
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="Search todos">
          <IconSearch size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="More">
          <IconDots size={16} stroke={1.5} />
        </IconButton>
      </ActionBarActions>
    </ActionBar>
  );
}
