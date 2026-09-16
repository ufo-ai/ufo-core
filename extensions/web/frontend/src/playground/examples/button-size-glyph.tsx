import { IconCheck } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";

export function ButtonSizeGlyph() {
  return (
    <Button variant="mark" size="glyph" aria-label="Done">
      <IconCheck stroke={1.5} aria-hidden />
    </Button>
  );
}
