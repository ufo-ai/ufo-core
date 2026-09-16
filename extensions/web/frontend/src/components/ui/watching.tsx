import { useState } from "react";

import { Button } from "@/components/ui/button";

const STOP = "Stop";

/** The one act a watched conversation offers while its turn runs: the stop the composer would
 *  carry, standing where the composer would. The surface decides who may press it. */
export function Watching({ onStop }: { onStop: () => Promise<void> }) {
  const [stopping, setStopping] = useState(false);
  return (
    <div className="mx-auto w-full max-w-page flex shrink-0 justify-end px-2xl py-lg">
      <Button
        variant="row"
        busy={stopping}
        onClick={async () => {
          setStopping(true);
          await onStop();
          setStopping(false);
        }}
      >
        {STOP}
      </Button>
    </div>
  );
}
