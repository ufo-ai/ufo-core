import { useRef, useState } from "react";

import { Menu, MenuButton, MenuContent, MenuItem, MenuLabel } from "@/blocks/menu";

const SNIPPETS = ["Blocked on the credential audit", "Waiting on the cutover window"];

const FIELD = {
  height: 28,
  flex: 1,
  padding: "0 8px",
  borderRadius: 6,
  background: "var(--blk-bg-200)",
  color: "var(--blk-text-1)",
  fontSize: 13,
};

export function ControlsMenuFocus() {
  const [note, setNote] = useState("");
  const field = useRef<HTMLInputElement>(null);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <Menu>
        <MenuButton>Insert</MenuButton>
        <MenuContent
          align="start"
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            field.current?.focus();
          }}
        >
          <MenuLabel>Snippet</MenuLabel>
          {SNIPPETS.map((snippet) => (
            <MenuItem key={snippet} onSelect={() => setNote(snippet)}>
              {snippet}
            </MenuItem>
          ))}
        </MenuContent>
      </Menu>
      <input
        ref={field}
        style={FIELD}
        aria-label="Note"
        placeholder="Note"
        value={note}
        onChange={(event) => setNote(event.target.value)}
      />
    </div>
  );
}
