import { IconCopy } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";

export function ButtonMark() {
  return (
    <Button variant="mark" size="glyph" aria-label="Copy">
      <IconCopy stroke={1.5} aria-hidden />
    </Button>
  );
}
