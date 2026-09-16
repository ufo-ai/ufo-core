import { IconLock, IconUsersGroup } from "@tabler/icons-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  audienceDetail,
  audienceLabel,
  isMemberAudience,
  useViewer,
  type AudienceEntry,
} from "@/lib/audience";

/** The lock's mass sits under its own box center, so the row's centering leaves the 16px glyph's
 *  ink centroid ~1px below the title text's cap center; one pixel up lands it on that center. */
const AUDIENCE_GLYPH = "relative -top-px size-(--size-glyph) shrink-0";

/** Who reads the conversation the member has open, standing as the glyph alone right of the name
 *  on the title line. It states today's audience and never an act: the marker answers a question
 *  the member asks while typing — who sees this — so it stands on the title line rather than
 *  behind a menu, and it says in the same breath that an admin can still open the thread, because
 *  a marker that reads as a promise of privacy is worse than none.
 *
 *  The glyph separates a private thread from a shared one at a glance and the word reaches the
 *  member on hover and on focus; the whole sentence rides in `title` and in the text beside the
 *  glyph for a reader who cannot see it, because "Only you" alone leaves the admin's reach
 *  implicit. */
export function AudienceMark({ entry }: { entry: AudienceEntry }) {
  const viewer = useViewer();
  const detail = audienceDetail(entry, viewer);
  const own = isMemberAudience(entry.audience);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-slot="audience"
          tabIndex={0}
          title={detail}
          className="flex shrink-0 items-center text-ink-soft"
        >
          {own ? (
            <IconLock className={AUDIENCE_GLYPH} aria-hidden />
          ) : (
            <IconUsersGroup className={AUDIENCE_GLYPH} aria-hidden />
          )}
          <span className="sr-only">{detail}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent side="bottom">{audienceLabel(entry, viewer)}</TooltipContent>
    </Tooltip>
  );
}
