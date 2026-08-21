import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { IconArrowRight } from "@tabler/icons-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

/** One thing offered on a list of things to open: its mark, what it is called, what it is, and the
 *  way in. The name and the line about it share one line and one truncation, so a long name takes
 *  the room a short one leaves rather than every row wrapping to the longest. The rows rule between
 *  themselves and the last one does not, because the list ends where the rows do. */
const ROW = cn(
  "group flex w-full items-center gap-md border-0 border-b border-edge bg-transparent",
  "px-md py-lg text-start text-ui text-inherit no-underline last:border-b-0 hover:bg-fill",
);

/** The way in is drawn only under the pointer: a column of arrows down a resting list is a column
 *  of marks saying the same thing about every row. */
const ARROW = cn(
  "size-(--size-glyph) shrink-0 text-ink-soft opacity-0 transition-opacity duration-100",
  "ease-control group-hover:opacity-100 group-focus-visible:opacity-100",
  "motion-reduce:transition-none",
);

/** Whether the line is cut, so a row that fits is never given a tooltip saying what it already
 *  says. */
function useCutLine(): [RefObject<HTMLSpanElement | null>, boolean] {
  const line = useRef<HTMLSpanElement>(null);
  const [cut, setCut] = useState(false);
  useEffect(() => {
    const node = line.current;
    if (!node) return;
    const measure = () => setCut(node.scrollWidth > node.clientWidth);
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(node);
    return () => watch.disconnect();
  }, []);
  return [line, cut];
}

/** A row that opens something. `onPress` where the caller holds the verb, `href` where the thing has
 *  an address of its own — the row is a link then, so it opens the way every other link does. */
export function PressRow({
  glyph,
  title,
  body,
  onPress,
  href,
}: {
  glyph: ReactNode;
  title: string;
  body: string;
  onPress?: () => void;
  href?: string;
}) {
  const [line, cut] = useCutLine();
  const inside = (
    <>
      {glyph}
      <span ref={line} className="min-w-0 flex-1 truncate">
        <span className="font-medium">{title}</span>
        <span className="text-ink-soft"> {body}</span>
      </span>
      <IconArrowRight className={ARROW} aria-hidden />
    </>
  );
  return (
    <Tooltip open={cut ? undefined : false}>
      <TooltipTrigger asChild>
        {href ? (
          <a href={href} className={ROW}>
            {inside}
          </a>
        ) : (
          <button type="button" onClick={onPress} className={ROW}>
            {inside}
          </button>
        )}
      </TooltipTrigger>
      <TooltipContent className="max-w-hint rounded-panel">
        <span className="font-medium">{title}</span> {body}
      </TooltipContent>
    </Tooltip>
  );
}

/** The row's own frame, for a row a caller draws itself — the connectors line under the starters is
 *  a row of this list that opens no thing on it. */
export { ROW as PRESS_ROW, ARROW as PRESS_ROW_ARROW };
