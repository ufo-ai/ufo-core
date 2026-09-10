import { useState } from "react";

import { Prompt, Prompts } from "@/blocks/action-bar";

const FILTERS = ["All", "#eng", "#social", "#business"];

export function PromptsActiveExample() {
  const [held, setHeld] = useState("All");
  return (
    <Prompts>
      {FILTERS.map((filter) => (
        <Prompt key={filter} active={filter === held} onClick={() => setHeld(filter)}>
          {filter}
        </Prompt>
      ))}
    </Prompts>
  );
}
