import { IconMicrophone, IconPlus } from "@tabler/icons-react";

import { Composer, IconButton } from "@/blocks/action-bar";

export function ComposerOutlineExample() {
  return (
    <div style={{ display: "grid", gap: 16, padding: 16, borderRadius: 8, background: "var(--blk-bg-200)" }}>
      <Composer
        placeholder="Ask Assistant anything..."
        leading={
          <IconButton label="Attach a file">
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
        }
        trailing={
          <IconButton label="Dictate">
            <IconMicrophone size={16} stroke={1.5} />
          </IconButton>
        }
      />
      <Composer
        surface="outline"
        placeholder="Ask Assistant anything..."
        leading={
          <IconButton label="Attach a file">
            <IconPlus size={16} stroke={1.5} />
          </IconButton>
        }
        trailing={
          <IconButton label="Dictate">
            <IconMicrophone size={16} stroke={1.5} />
          </IconButton>
        }
      />
    </div>
  );
}
