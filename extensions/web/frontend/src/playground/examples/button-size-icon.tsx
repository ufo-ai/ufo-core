import { IconTerminal2 } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";

export function ButtonSizeIcon() {
  return (
    <Button variant="outline" size="icon" aria-label="Shell">
      <IconTerminal2 aria-hidden />
    </Button>
  );
}
