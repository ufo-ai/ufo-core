import { IconBrandSlack, IconMessageCircle, IconTerminal2 } from "@tabler/icons-react";
import type { ReactNode } from "react";

import {
  IMESSAGE_SURFACE,
  SLACK_SURFACE,
  UFO_SURFACE,
  origin,
  slackLink,
  surfaceWord,
} from "@/lib/audience";
import type { Conversation } from "@/lib/types";

const SURFACE_GLYPH = "size-(--size-glyph) shrink-0 text-ink-faint";

/** The surface a conversation came in on, drawn at the far end of its row in a listing of them. The
 *  portal draws none: a listing of conversations is read in the portal, so a glyph on every row
 *  would state where the member already is. The words for the same fact stay in the row beside it,
 *  which is what a reader unable to see the glyph gets.
 *
 *  Since most rows carry no glyph, one drawn ahead of the title would indent that row alone and
 *  leave the list without a left edge to read down. It trails instead, where it marks the few rows
 *  that have it without moving the many that do not, and it is drawn faint: a title is what the
 *  member scans for, and the surface is the answer to a question they have already asked.
 *
 *  It is drawn at `--size-glyph`, the size every other mark beside a row takes, rather than at the
 *  row's own text size: a glyph scaled to a 13px label is read as a smudge beside a title that runs
 *  the width of the column, and a mark nobody can name states no surface. */
export function SurfaceGlyph({ surface }: { surface: string }) {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={SURFACE_GLYPH} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={SURFACE_GLYPH} aria-hidden />;
  if (surface === IMESSAGE_SURFACE) {
    return <IconMessageCircle className={SURFACE_GLYPH} aria-hidden />;
  }
  return null;
}

const SURFACE_MARK = "size-(--size-surface-mark) shrink-0";

function surfaceMark(surface: string): ReactNode {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={SURFACE_MARK} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={SURFACE_MARK} aria-hidden />;
  if (surface === IMESSAGE_SURFACE) {
    return <IconMessageCircle className={SURFACE_MARK} aria-hidden />;
  }
  return null;
}

export function SurfaceMark({ conversation }: { conversation: Conversation }) {
  const mark = surfaceMark(conversation.surface);
  if (mark === null) return null;
  const where = origin(conversation);
  const href = slackLink(conversation.surface, conversation.source);
  if (href === null) {
    return (
      <span className="flex items-center gap-xs whitespace-nowrap text-ink-soft">
        {mark}
        {where}
      </span>
    );
  }
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={"Open " + where + " in " + surfaceWord(conversation.surface)}
      className="flex items-center gap-xs whitespace-nowrap text-inherit no-underline hover:underline focus-visible:underline"
    >
      {mark}
      {where} <span className="text-ink-soft">↗</span>
    </a>
  );
}
