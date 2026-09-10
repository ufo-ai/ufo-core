import { IconArrowUp, IconPlus } from "@tabler/icons-react";

import { Composer, IconButton } from "@/blocks/action-bar";

export function ComposerFloatingExample() {
  return (
    <Composer
      floating
      placeholder="Ask Assistant anything..."
      leading={
        <IconButton label="Attach a file">
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
      }
      trailing={
        <IconButton label="Send" type="submit">
          <IconArrowUp size={16} stroke={1.5} />
        </IconButton>
      }
    />
  );
}
