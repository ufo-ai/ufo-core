import { IconFilter2, IconPlus, IconSparkles } from "@tabler/icons-react";

import { Composer, IconButton, Prompt, Prompts, SearchField } from "@/blocks/action-bar";

export function ActionBarStates() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, width: 349 }}>
      <Prompts>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Summarize today</Prompt>
        <Prompt active>Open</Prompt>
        <Prompt disabled>Archived</Prompt>
      </Prompts>
      <Prompts wrap>
        <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Summarize today</Prompt>
        <Prompt active>Open</Prompt>
        <Prompt disabled>Archived</Prompt>
        <Prompt>Blocked</Prompt>
      </Prompts>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <IconButton label="Add">
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="Filter" active>
          <IconFilter2 size={16} stroke={1.5} />
        </IconButton>
        <IconButton label="Add to archive" disabled>
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
        <SearchField value="migration" onChange={() => undefined} onClear={() => undefined} />
      </div>
      <Composer placeholder="Ask Assistant anything..." />
      <Composer placeholder="Ask Assistant anything..." disabled />
      <Composer placeholder="Ask Assistant anything..." value="Summarize today" busy />
    </div>
  );
}
