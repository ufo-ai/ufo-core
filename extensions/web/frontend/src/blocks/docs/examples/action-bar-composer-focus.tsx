import { useState } from "react";
import { IconArrowUp } from "@tabler/icons-react";

import { Composer, IconButton, Prompt, Prompts } from "@/blocks/action-bar";

const CHIPS = ["Summarize today", "List the blocked connectors", "Draft the audit request"];

export function ComposerFocusExample() {
  const [draft, setDraft] = useState("");
  const [picks, setPicks] = useState(0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Prompts wrap>
        {CHIPS.map((chip) => (
          <Prompt
            key={chip}
            onClick={() => {
              setDraft(chip);
              setPicks(picks + 1);
            }}
          >
            {chip}
          </Prompt>
        ))}
      </Prompts>
      <Composer
        placeholder="Ask Assistant anything..."
        value={draft}
        focusKey={picks}
        onChange={setDraft}
        onSubmit={() => setDraft("")}
        trailing={
          <IconButton label="Send" type="submit">
            <IconArrowUp size={16} stroke={1.5} />
          </IconButton>
        }
      />
    </div>
  );
}
