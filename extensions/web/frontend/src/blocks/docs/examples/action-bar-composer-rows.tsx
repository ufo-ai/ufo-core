import { IconArrowUp, IconMicrophone, IconPlus } from "@tabler/icons-react";

import { Composer, IconButton } from "@/blocks/action-bar";

export function ComposerRowsExample() {
  return (
    <Composer
      rows={2}
      placeholder="Ask Assistant anything..."
      leading={
        <IconButton label="Attach a file">
          <IconPlus size={16} stroke={1.5} />
        </IconButton>
      }
      trailing={
        <>
          <IconButton label="Dictate">
            <IconMicrophone size={16} stroke={1.5} />
          </IconButton>
          <IconButton label="Send" type="submit">
            <IconArrowUp size={16} stroke={1.5} />
          </IconButton>
        </>
      }
    />
  );
}
