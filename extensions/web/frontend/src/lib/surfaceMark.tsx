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
import type { OwnedConversation } from "@/lib/types";

const SURFACE_GLYPH = "size-(--size-glyph) shrink-0 text-ink-faint";

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

export function SurfaceMark({ conversation }: { conversation: OwnedConversation }) {
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
