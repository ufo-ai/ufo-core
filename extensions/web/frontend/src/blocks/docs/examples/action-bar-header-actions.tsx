import { IconCalendar, IconCalendarShare, IconDots, IconPlus, IconSearch } from "@tabler/icons-react";

import { ActionBar, ActionBarActions, ActionBarTitle, IconButton } from "@/blocks/action-bar";

export function HeaderActionsExample() {
  return (
    <ActionBar>
      <ActionBarTitle icon={<IconCalendar size={16} stroke={1.5} />} menu>
        Meetings
      </ActionBarTitle>
      <ActionBarActions>
        <IconButton label="New meeting">
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="Share calendar">
          <IconCalendarShare size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="Search meetings">
          <IconSearch size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="More">
          <IconDots size={16} stroke={1.5} />
        </IconButton>
      </ActionBarActions>
    </ActionBar>
  );
}
