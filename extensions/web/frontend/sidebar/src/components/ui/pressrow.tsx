import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { IconChevronRight } from "@tabler/icons-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

/** One thing offered on a list of things to open: its mark, what it says, and the way in. What the
 *  row says shares one line and one truncation, so a long sentence takes the room a short one
 *  leaves rather than every row wrapping to the longest. A row is one row
 *  high whatever it says, so a list of them is a rhythm the eye reads down rather than a stack of
 *  boxes each sized by its own words. Nothing rules between them — the rows are close enough to
 *  read as one list, and a rule between rows one row apart is a stripe pattern rather than a
 *  separation. */
const ROW = cn(
  "group flex h-(--size-row) w-full items-center gap-sm border-0 bg-transparent",
  "px-lg text-start text-ui text-inherit no-underline hover:bg-fill",
);

/** The way in, drawn on every row at rest: the chevron is what says a row opens something, and a
 *  row that only admits it under the pointer reads as text until the member happens to cross it. */
const CHEVRON = "size-(--size-glyph) shrink-0 text-ink-soft";

/** The stamp at the row's end: held in the layout at rest and faded in on the row the pointer or
 *  the keyboard is on, so the row it belongs to is the one that states it. */
const STAMP = cn(
  "shrink-0 text-ink-soft opacity-0 transition-opacity duration-100 ease-control",
  "group-hover:opacity-100 group-focus-visible:opacity-100",
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
 *  an address of its own — the row is a link then, so it opens the way every other link does.
 *
 *  `line` is what the row says: one sentence that reads on its own. `note` is the trailing detail
 *  a few rows carry — what a listed row costs, or when it last moved — and it is set in the same
 *  type as the line, because a row drawn in two registers is a row the eye assembles out of two
 *  pieces rather than reads as one thing.
 *
 *  `when` is the stamp a list of one kind of record scans down: it stands at the row's own end,
 *  muted, and shows under the pointer, because a column of dates every row carries is the same
 *  fact repeated against the names the member came to read. It holds its width at rest, so a name
 *  is cut to the same measure whether the pointer is on the row or not, and it is drawn rather than
 *  withheld, so a reader who never hovers is still told it.
 *
 *  `glyph` is the mark for the kind of thing the row opens, and a list whose rows are all one kind
 *  draws none: a mark repeated down every row states nothing that tells two rows apart, and takes
 *  the width the line reads in. */
export function PressRow({
  glyph,
  line,
  note,
  when,
  onPress,
  href,
}: {
  glyph?: ReactNode;
  line: string;
  note?: string;
  when?: string;
  onPress?: () => void;
  href?: string;
}) {
  const [measured, cut] = useCutLine();
  const says = note ? line + " " + note : line;
  const inside = (
    <>
      {glyph}
      <span ref={measured} className="min-w-0 flex-1 truncate">
        {says}
      </span>
      {when ? <span className={STAMP}>{when}</span> : null}
      <IconChevronRight className={CHEVRON} aria-hidden />
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
        {says}
      </TooltipContent>
    </Tooltip>
  );
}

/** The row's own frame, for a row a caller draws itself — the connectors line under the starters is
 *  a row of this list that opens no thing on it. */
export { ROW as PRESS_ROW, CHEVRON as PRESS_ROW_CHEVRON };
