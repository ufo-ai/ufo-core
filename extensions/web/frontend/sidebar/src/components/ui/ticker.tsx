import { useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/cn";

/** How a line too long for its column states its tail while the member is on it. The pace is a
 *  reading one rather than a duration, so a line that overruns by a word and one that overruns by a
 *  sentence both pass the frame at the speed they are read at. The rest is what the pointer pays to
 *  start it: crossing a column on the way somewhere else passes over every row in it, and a line
 *  that began travelling on contact would set the whole column moving. The way back is a return
 *  rather than a reading, so it takes one duration and no rest. */
const TICKER_SPEED = 45;
const TICKER_REST_MS = 300;
const TICKER_BACK_MS = 150;

/** A line held to its column, which travels far enough left to state its tail while the row it sits
 *  on is under the pointer or holds the keyboard.
 *
 *  The travel is measured at the moment it is asked for rather than held: a line that fits moves
 *  nothing, and one measured before the face it is set in had loaded would travel the wrong
 *  distance. It is the words' own width that is measured, not the frame's overflow, which reports
 *  the ellipsis rather than the text behind it.
 *
 *  The resting line is an inline run so that the frame ellipses it, the way every other truncated
 *  row in the sidebar is drawn; the moment it travels it becomes a box, because a transform does
 *  not move an inline one. The ellipsis goes with it — a mark that says "there is more" has nothing
 *  to say while the more is being read, and left standing it sits over the moving words.
 *
 *  A row states more than one line, and one hover moves all of them: the row owns the ask and each
 *  line measures its own overrun, so a name that fits stays still beside a line that does not.
 *
 *  The ask is counted rather than held as a flag, because every ask is a fresh measurement: a row
 *  the pointer is already resting on can be reached again by the keyboard, and the words under it
 *  can have changed width since the pointer landed. Zero is rest. */
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
