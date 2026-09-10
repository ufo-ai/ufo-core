import { IconLock, IconUsers } from "@tabler/icons-react";

import {
  audienceDetail,
  audienceLabel,
  isMemberAudience,
  useViewer,
  type AudienceEntry,
} from "@/lib/audience";

const AUDIENCE_GLYPH = "size-(--size-glyph) shrink-0";

/** Who reads the conversation the member has open, drawn beside its name in every pane that
 *  draws one. It states today's audience and never an act: the marker answers a question the
 *  member asks while typing — who sees this — so it stands in the header rather than behind a
 *  menu, and it says in the same breath that an admin can still open the thread, because a marker
 *  that reads as a promise of privacy is worse than none.
 *
 *  The word is the answer and the glyph separates a private thread from a shared one at a glance;
 *  the whole sentence rides in `title` and for a reader who cannot see the marker in the text
 *  beside it, because "Only you" alone leaves the admin's reach implicit. */
export function AudienceMark({ entry }: { entry: AudienceEntry }) {
  const viewer = useViewer();
  const detail = audienceDetail(entry, viewer);
  const own = isMemberAudience(entry.audience);
  return (
    <span
      data-slot="audience"
      title={detail}
      className="flex items-center gap-2xs whitespace-nowrap text-ink-soft"
    >
      {own ? (
        <IconLock className={AUDIENCE_GLYPH} aria-hidden />
      ) : (
        <IconUsers className={AUDIENCE_GLYPH} aria-hidden />
      )}
      {audienceLabel(entry, viewer)}
      <span className="sr-only">{detail}</span>
    </span>
  );
}
