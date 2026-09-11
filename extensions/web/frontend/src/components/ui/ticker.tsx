import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";

const TICKER_SPEED = 45;
const TICKER_REST_MS = 300;
const TICKER_BACK_MS = 150;

/** `--spacing-2xl`, the measure `line-fade-x` cuts with: the travel clears it, so the last word
 *  stops short of the fade rather than inside it. */
const TICKER_FADE = 16;

/** It is the words' own width that is measured, not the frame's overflow, which reports the cut
 *  rather than the text behind it. A line too long for its column ends in a fade, so the words the
 *  column swallowed read as continuing rather than as three dots standing for them; a line out
 *  travelling cuts at the leading edge instead, since its tail is the half the reader came for.
 *  The rest before the travel is a timer rather than a transition delay, so the cut changes ends at
 *  the moment the words move. The resting line is inline, since a transform does not move one. */
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
  const [over, setOver] = useState(0);
  const [shift, setShift] = useState(0);
  useEffect(() => {
    const held = frame.current;
    const line = words.current;
    if (!held || !line) return;
    const measure = () =>
      setOver(Math.max(0, Math.round(line.getBoundingClientRect().width - held.clientWidth)));
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(held);
    watch.observe(line);
    return () => watch.disconnect();
  }, [children, asks]);
  useEffect(() => {
    if (asks === 0 || over === 0) {
      setShift(0);
      return;
    }
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const rest = window.setTimeout(() => setShift(over + TICKER_FADE), TICKER_REST_MS);
    return () => window.clearTimeout(rest);
  }, [asks, over]);
  return (
    <span
      ref={frame}
      className={cn(
        "block min-w-0 overflow-hidden text-clip whitespace-nowrap",
        over > 0 && (shift > 0 ? "line-fade-x" : "line-fade-e"),
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
        }}
      >
        {children}
      </span>
    </span>
  );
}
