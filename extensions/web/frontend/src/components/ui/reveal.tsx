import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** Content whose length the member cannot predict — a prompt, a transcript — is held to one screen
 *  so the sections beneath it stay reachable, and opens when they ask for it. The control is drawn
 *  only when something sits behind the fold: a block that already fits is never offered an
 *  expansion that would do nothing. While open the measurement is held rather than retaken — an
 *  expanded block always fits its own height, and re-measuring would take its own control away. */
export function Reveal({ children }: { children: ReactNode }) {
  const frame = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [over, setOver] = useState(false);
  const region = useId();

  useLayoutEffect(() => {
    const box = frame.current;
    if (!box || open) return;
    const measure = () => setOver(box.scrollHeight > box.clientHeight);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open, children]);

  return (
    <div className="rounded-panel border border-edge bg-surface">
      <div
        id={region}
        ref={frame}
        className={cn("relative overflow-hidden", !open && "max-h-(--size-reveal)")}
      >
        <div className="p-xl">{children}</div>
        {over && !open ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 bottom-0 h-6xl bg-linear-to-t from-surface"
          />
        ) : null}
      </div>
      {over ? (
        <div className="border-t border-edge-soft p-md text-center">
          <Button
            variant="row"
            aria-expanded={open}
            aria-controls={region}
            onClick={() => setOpen((shown) => !shown)}
          >
            {open ? "Show less" : "Show more"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
