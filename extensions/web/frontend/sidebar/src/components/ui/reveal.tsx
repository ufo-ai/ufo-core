import { useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

/** Content whose length the member cannot predict — a prompt, a run's answer, a line an agent
 *  wrote — is held to one screen so what sits beneath it stays reachable, and opens when the member
 *  asks for it. The control is drawn only when something sits behind the fold: a block that already
 *  fits is never offered an expansion that would do nothing. While open the measurement is held
 *  rather than retaken — an expanded block always fits its own height, and re-measuring would take
 *  its own control away.
 *
 *  `bare` drops the card. A fold standing among a section's tables and forms is a card like they
 *  are; one inside a reply's activity tree has no card beside it, and a border and a centred
 *  footer there would state a surface the mono lines around it do not have. */
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
