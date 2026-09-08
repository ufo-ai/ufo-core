import { useState, type ReactNode } from "react";
import { IconChevronRight } from "@tabler/icons-react";

import { Ticker } from "@/components/ui/ticker";
import { cn } from "@/lib/cn";
import { Moment } from "@/lib/moments";

const ROW = cn(
  "group flex h-(--size-row) w-full items-center gap-sm border-0 bg-transparent",
  "px-lg text-start text-ui text-inherit no-underline hover:bg-fill",
);

const CHEVRON = "size-(--size-glyph) shrink-0 text-ink-soft";

const STAMP = cn(
  "shrink-0 text-ink-soft opacity-0 transition-opacity duration-100 ease-control",
  "group-hover:opacity-100 group-focus-visible:opacity-100",
);

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

export { ROW as PRESS_ROW, CHEVRON as PRESS_ROW_CHEVRON };
