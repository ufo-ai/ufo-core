import { IconFilter2, IconSparkles } from "@tabler/icons-react";

import { ActionBar, ActionBarActions, IconButton, Prompt, Prompts, SearchField } from "@/blocks/action-bar";

export function ToolbarExample() {
  return (
    <ActionBar variant="toolbar">
      <Prompts>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Summarize</Prompt>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Brainstorm</Prompt>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Delegate</Prompt>
      </Prompts>
      <ActionBarActions>
        <IconButton label="Filter leads">
          <IconFilter2 size={16} stroke={1.5} />
        </IconButton>
        <SearchField />
      </ActionBarActions>
    </ActionBar>
  );
}
