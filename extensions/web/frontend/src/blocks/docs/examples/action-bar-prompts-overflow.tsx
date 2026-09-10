import { IconSparkles } from "@tabler/icons-react";

import { Prompt, Prompts } from "@/blocks/action-bar";

const SUGGESTIONS = [
  "Summarize todos",
  "Brainstorm ideas",
  "Delegate to Assistant",
  "Draft a reply",
  "Group by project",
  "Plan the week",
];

export function PromptsOverflowExample() {
  return (
    <div style={{ width: 349 }}>
      <Prompts>
        {SUGGESTIONS.map((suggestion) => (
          <Prompt key={suggestion} icon={<IconSparkles size={16} stroke={1.5} />}>
            {suggestion}
          </Prompt>
        ))}
      </Prompts>
    </div>
  );
}
