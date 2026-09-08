import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";

const TICKER_SPEED = 45;
const TICKER_REST_MS = 300;
const TICKER_BACK_MS = 150;

/** It is the words' own width that is measured, not the frame's overflow, which reports the ellipsis
 *  rather than the text behind it. The resting line is inline, since a transform does not move one. */
export function Ticker({
  asks,
  className,
  children,
}: {
  asks: number;
  className?: string;
  children: ReactNode;
}) {
  const frame = useRef<HTMLSpanElement>(null);
  const words = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);
  useEffect(() => {
    if (asks === 0) {
      setShift(0);
      return;
    }
    const held = frame.current;
    const line = words.current;
    if (!held || !line) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    setShift(Math.max(0, Math.round(line.getBoundingClientRect().width - held.clientWidth)));
  }, [asks, children]);
  return (
    <span
      ref={frame}
      className={cn(
        "block min-w-0 overflow-hidden whitespace-nowrap",
        shift ? "text-clip" : "text-ellipsis",
        className,
      )}
    >
      <span
        ref={words}
        className={cn("transition-transform ease-linear", shift ? "inline-block" : "inline")}
        style={{
          transform: `translateX(${-shift}px)`,
          transitionDuration: shift
            ? Math.round((shift / TICKER_SPEED) * 1000) + "ms"
            : TICKER_BACK_MS + "ms",
          transitionDelay: shift ? TICKER_REST_MS + "ms" : "0ms",
        }}
      >
        {children}
      </span>
    </span>
  );
}
