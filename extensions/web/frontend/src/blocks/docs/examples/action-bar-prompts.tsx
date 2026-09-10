import { IconSparkles } from "@tabler/icons-react";

import { Prompt, Prompts } from "@/blocks/action-bar";

export function PromptsExample() {
  return (
    <Prompts>
      <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Summarize todos</Prompt>
      <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Brainstorm ideas</Prompt>
      <Prompt icon={<IconSparkles size={16} stroke={1.5} />}>Delegate to Assistant</Prompt>
    </Prompts>
  );
}
