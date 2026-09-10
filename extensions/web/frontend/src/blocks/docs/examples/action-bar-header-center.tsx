import { IconDots, IconMessage } from "@tabler/icons-react";

import {
  ActionBar,
  ActionBarActions,
  ActionBarCenter,
  ActionBarTitle,
  IconButton,
} from "@/blocks/action-bar";

function Actions() {
  return (
    <ActionBarActions>
      <IconButton label="More">
        <IconDots size={16} stroke={1.5} />
      </IconButton>
      <IconButton label="Open the conversation">
        <IconMessage size={16} stroke={1.5} />
      </IconButton>
    </ActionBarActions>
  );
}

export function HeaderCenterExample() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <ActionBar>
        <ActionBarTitle>Issues</ActionBarTitle>
        <ActionBarCenter>180 open issues · 21 unread</ActionBarCenter>
        <Actions />
      </ActionBar>
      <ActionBar>
        <ActionBarTitle>Issues</ActionBarTitle>
        <ActionBarCenter>
          180 open issues · 177 carry no comment from me · first triage today at 4:00 PM · nothing
          approved to implement
        </ActionBarCenter>
        <Actions />
      </ActionBar>
    </div>
  );
}
