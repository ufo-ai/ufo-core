import { useState, type ReactNode } from "react";
import { IconChevronRight } from "@tabler/icons-react";

import { Ticker } from "@/components/ui/ticker";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";

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

/** A row that opens something. `onPress` where the caller holds the verb, `href` where the thing has
 *  an address of its own — the row is a link then, so it opens the way every other link does.
 *
 *  `line` is what the row says: one sentence that reads on its own, cut to the measure the row
 *  leaves it and travelling out to its last word while the pointer or the keyboard is on the row —
 *  the way a name in the sidebar states its tail, so a lane too narrow for a title is not where the
 *  title goes unread. `note` is the trailing detail a few rows carry — what a listed row costs, or
 *  when it last moved — and it is set in the same type as the line, because a row drawn in two
 *  registers is a row the eye assembles out of two pieces rather than reads as one thing.
 *
 *  `when` is the moment the record last moved, as the stamp it was sent in: a list of one kind of
 *  record scans that column down. It stands at the row's own end, muted, and shows under the
 *  pointer, because a date every row carries is the same fact repeated against the names the member
 *  came to read. It reads as the distance from now while that is what says a row is recent, and as
 *  its own day once the day is what matters — so the column is a few characters wide rather than a
 *  full date, and the words the row came to say keep the width. It holds that width at rest, so a
 *  name is cut to the same measure whether the pointer is on the row or not, and it is drawn rather
 *  than withheld, so a reader who never hovers is still told it.
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
  /* Every ask is a fresh measurement rather than a flag, the way the sidebar's rows count theirs: a
     row the pointer already rests on can be reached again by the keyboard, and the words under it
     can have changed width since it landed. Zero is rest. */
  const [asks, setAsks] = useState(0);
  const says = note ? line + " " + note : line;
  const held = {
    onPointerEnter: () => setAsks((asked) => asked + 1),
    onPointerLeave: () => setAsks(0),
    onFocus: () => setAsks((asked) => asked + 1),
    onBlur: () => setAsks(0),
  };
  const inside = (
    <>
      {glyph}
      <Ticker asks={asks} className="min-w-0 flex-1">
        {says}
      </Ticker>
      {when ? (
        <span className={STAMP}>
          <Moment at={when} />
        </span>
      ) : null}
      <IconChevronRight className={CHEVRON} aria-hidden />
    </>
  );
  return href ? (
    <a href={href} className={ROW} {...held}>
      {inside}
    </a>
  ) : (
    <button type="button" onClick={onPress} className={ROW} {...held}>
      {inside}
    </button>
  );
}

/** The row's own frame, for a row a caller draws itself — the connectors line under the starters is
 *  a row of this list that opens no thing on it. */
export { ROW as PRESS_ROW, CHEVRON as PRESS_ROW_CHEVRON };
