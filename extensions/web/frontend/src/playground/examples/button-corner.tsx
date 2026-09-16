import { IconX } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";

export function ButtonCorner() {
  return (
    <Button variant="corner" aria-label="Remove">
      <IconX className="size-(--size-glyph)" stroke={1.5} aria-hidden />
    </Button>
  );
}
