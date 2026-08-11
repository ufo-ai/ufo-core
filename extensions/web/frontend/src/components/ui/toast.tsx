import { useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

const TOAST_DWELL_MS = 6000;

export type ToastState = { title: string; description?: string };

export const SILENT: ToastState = { title: "" };

/** A toast reports an outcome whose surface has already gone, or a read the member cannot correct
 *  from any control on the screen. The title names what happened; the description names why. A
 *  refusal a field can answer stays beside that field instead, because a message that dismisses
 *  itself cannot be read back. */
export function Toast({ state, onDone }: { state: ToastState; onDone: () => void }) {
  const done = useRef(onDone);
  done.current = onDone;
  const { title, description } = state;
  useEffect(() => {
    if (!title) return;
    const timer = setTimeout(() => done.current(), TOAST_DWELL_MS);
    return () => clearTimeout(timer);
  }, [title, description]);
  if (!title) return null;
  return (
    <div
      role="status"
      className={cn(
        "fixed bottom-2xl left-2xl z-10 max-w-empty",
        "rounded-panel border border-edge-strong bg-surface px-lg py-md",
        "[box-shadow:var(--shadow-raised)] animate-raise",
      )}
    >
      <p className="text-ui font-strong">{title}</p>
      {description ? <p className="text-small opacity-(--muted) mt-hair">{description}</p> : null}
    </div>
  );
}
