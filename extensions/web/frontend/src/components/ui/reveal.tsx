import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export function Reveal({ children, bare = false }: { children: ReactNode; bare?: boolean }) {
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
    <div data-slot="reveal" className={cn(!bare && "rounded-panel border border-edge bg-card text-card-foreground")}>
      <div
        id={region}
        ref={frame}
        className={cn("relative overflow-hidden", !open && "max-h-(--size-reveal)")}
      >
        <div className={cn(!bare && "p-xl")}>{children}</div>
        {over && !open ? (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 bottom-0 h-7xl bg-linear-to-t from-card"
          />
        ) : null}
      </div>
      {over ? (
        <div className={cn(bare ? "pt-2xs" : "border-t border-edge p-md text-center")}>
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
