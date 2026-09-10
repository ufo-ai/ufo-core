import { useState } from "react";
import { IconDots, IconFlag, IconPlus, IconSearch } from "@tabler/icons-react";

import { ActionBarActions, IconButton } from "@/blocks/action-bar";

export function IconButtonStatesExample() {
  const [priority, setPriority] = useState(false);
  return (
    <ActionBarActions>
      <IconButton label="New todo">
        <IconPlus size={16} stroke={1.5} />
      </IconButton>
      <IconButton label="Search todos" data-state="hover">
        <IconSearch size={16} stroke={1.5} />
      </IconButton>
      <IconButton label="More" active>
        <IconDots size={16} stroke={1.5} />
      </IconButton>
      <IconButton label="Priority" pressed={priority} onClick={() => setPriority(!priority)}>
        <IconFlag size={16} stroke={1.5} />
      </IconButton>
    </ActionBarActions>
  );
}
