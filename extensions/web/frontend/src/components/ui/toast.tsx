import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

const TOAST_DWELL_MS = 6000;

/** A toast reports an act whose surface has already gone. It never carries a refusal: a refusal
 *  belongs beside the control that can correct it, and a message that dismisses itself cannot be
 *  read back. */
export function Toast({ message, onDone }: { message: string; onDone: () => void }) {
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => done.current(), TOAST_DWELL_MS);
    return () => clearTimeout(timer);
  }, [message]);
  if (!message) return null;
  return (
    <div
      role="status"
      className={cn(
        "fixed bottom-2xl left-2xl z-10 max-w-empty",
        "rounded-panel border border-edge-strong bg-surface px-lg py-md text-ui",
        "[box-shadow:var(--shadow-raised)] animate-raise",
      )}
    >
      {message}
    </div>
  );
}
