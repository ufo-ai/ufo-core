import { IconSparkles } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";

export function ButtonSizeChip() {
  return (
    <Button variant="quiet" size="chip">
      <IconSparkles className="size-(--size-glyph)" stroke={1.5} aria-hidden />
      Auto
    </Button>
  );
}
