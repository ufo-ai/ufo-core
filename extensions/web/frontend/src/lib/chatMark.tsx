import {
  IconArrowUpRight,
  IconBrandSlack,
  IconBrowser,
  IconHistoryToggle,
  IconLoader,
  IconMessageCircle,
  IconPinFilled,
  IconTerminal2,
  IconUsersGroup,
} from "@tabler/icons-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  IMESSAGE_SURFACE,
  SHARED_SUBJECT,
  SLACK_SURFACE,
  UFO_SURFACE,
  isPortalChat,
  slackLink,
  surfaceWord,
} from "@/lib/audience";
import { automationId } from "@/lib/automationLane";
import { cn } from "@/lib/cn";
import { chatState, type ChatState } from "@/lib/rail";
import type { Conversation } from "@/lib/types";
import { automationsHash } from "@/lib/route";

export const AUTOMATION = "Automation";

export const SHARED = "Shared with Workspace";

export const PINNED = "Pinned";

const GLYPH = "size-(--size-glyph) shrink-0";

const MARK = "size-3.5 shrink-0";

const DOT = "size-xs rounded-full bg-current";

/** The picture a surface is drawn by, wherever the portal draws one. */
function ChannelGlyph({ surface, className }: { surface: string; className: string }) {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={className} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={className} aria-hidden />;
  if (surface === IMESSAGE_SURFACE) return <IconMessageCircle className={className} aria-hidden />;
  return <IconBrowser className={className} aria-hidden />;
}

function Spinner({ glyph: Glyph, ink }: { glyph: typeof IconLoader; ink: string }) {
  return <Glyph className={cn(GLYPH, "animate-spin motion-reduce:animate-none", ink)} aria-hidden />;
}

const STATE_WORDS: Record<ChatState, string> = {
  working: "Working",
  waiting: "Waiting for you",
  unread: "Unread",
  idle: "Idle",
};

const STATE_INK: Record<Exclude<ChatState, "working">, string> = {
  waiting: "text-blocked",
  unread: "text-live",
  idle: "text-ink-quiet",
};

/** The one mark a conversation leads with, on the table and down the rail alike: the state alone
 *  and never the surface, which has a column of its own on one and the row's end on the other. It
 *  states the engine's turn beside the state it read off it, because a turn that lands is what a
 *  stream watches while the state is what the ink draws. A pinned row draws that state as a pin in
 *  the dot's place and the dot's ink, so the row that stands above the list says so. */
export function ChatStatus({ row, className }: { row: Conversation; className?: string }) {
  const state = chatState(row);
  return (
    <span
      role="img"
      aria-label={row.pinned ? PINNED + ", " + STATE_WORDS[state] : STATE_WORDS[state]}
      data-state={state}
      data-pinned={row.pinned}
      data-turn={row.turn}
      className={cn("flex items-center justify-center", GLYPH, className)}
    >
      {state === "working" ? (
        <Spinner glyph={IconLoader} ink="text-ink-soft" />
      ) : row.pinned ? (
        <IconPinFilled aria-hidden className={cn(MARK, STATE_INK[state])} />
      ) : (
        <span aria-hidden className={cn(DOT, STATE_INK[state])} />
      )}
    </span>
  );
}

/** What a row carries beyond its title: where it came in, what drove it, and who else can reach
 *  it — each drawn only where it is true, so the ordinary conversation states nothing. The table
 *  gives these a column and a tooltip each; the rail has one row's end for all three and draws
 *  them named for a reader and silent under the pointer, because the row already opens a card. */
export function ChatTrail({ row }: { row: Conversation }) {
  const mark = cn(MARK, "text-ink-soft");
  return (
    <>
      {isPortalChat(row.surface) ? null : (
        <span role="img" aria-label={channelWord(row.surface)} className="flex">
          <ChannelGlyph surface={row.surface} className={mark} />
        </span>
      )}
      {row.automation_name === null ? null : (
        <IconHistoryToggle className={mark} role="img" aria-label={AUTOMATION} />
      )}
      {row.audience === SHARED_SUBJECT ? (
        <IconUsersGroup className={mark} role="img" aria-label={SHARED} />
      ) : null}
    </>
  );
}

/** A tooltip that leads somewhere, so the words that name a mark are also the way to reach what
 *  they name. It is hoverable by default, which is what lets a pointer cross into the link. */
function LinkedTip({ at, says }: { at: string; says: string }) {
  return (
    <TooltipContent side="top" className="p-0">
      <a
        href={at}
        className="flex items-center gap-2xs px-2xl py-sm text-inherit no-underline hover:underline"
      >
        {says}
        <IconArrowUpRight className="size-(--size-glyph) shrink-0" aria-hidden />
      </a>
    </TooltipContent>
  );
}

/** Who can reach the chat, drawn only where that is anyone but its owner: a row carrying no mark is
 *  private, which is the case that needs no stating. It stands beside the automation mark rather
 *  than in a column, because most rows hold neither and a column of blanks is width the titles
 *  want. */
export function ShareMark({ row }: { row: Conversation }) {
  if (row.audience !== SHARED_SUBJECT) return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <IconUsersGroup className={cn(MARK, "text-ink-soft")} role="img" aria-label={SHARED} />
      </TooltipTrigger>
      <TooltipContent side="top">{SHARED}</TooltipContent>
    </Tooltip>
  );
}

/** The mark names the automation that last fired here and leads to it: an automation runs on its
 *  own schedule, so what a member wants from its chat is usually the thing that scheduled it. */
export function AutomationMark({ row }: { row: Conversation }) {
  const name = row.automation_name;
  const kind = row.automation_kind;
  const mark = (
    <IconHistoryToggle className={cn(MARK, "text-ink-soft")} role="img" aria-label={AUTOMATION} />
  );
  if (name === null || kind === null) return mark;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{mark}</TooltipTrigger>
      <LinkedTip
        at={automationsHash({ opens: [automationId({ agent: row.agent_id, kind, name })] })}
        says={row.automation_title || name}
      />
    </Tooltip>
  );
}

/** The portal is `Web` in a column of channels, where `Portal` names the product rather than the
 *  place a member was sitting when they said it. */
export function channelWord(surface: string): string {
  return isPortalChat(surface) ? "Web" : surfaceWord(surface);
}


/** Where the conversation came in, drawn only where that is not the portal: most chats are the
 *  member's own web ones, so a mark on every row would say nothing and the rows that arrived from
 *  somewhere else would stop standing out.
 *
 *  The glyph is the whole of it and the words are under the pointer — the surface's name, or the
 *  room it ran in where the surface reported one, because one Slack workspace fills a column with
 *  `#`-prefixed names that differ only in their last word. A room leads back out to the thread. */
export function ChannelMark({ row }: { row: Conversation }) {
  if (isPortalChat(row.surface)) return null;
  const word = channelWord(row.surface);
  const mark = (
    <span role="img" aria-label={row.surface_label ?? word} className="flex">
      <ChannelGlyph surface={row.surface} className={cn(MARK, "text-ink-soft")} />
    </span>
  );
  const out = slackLink(row.surface, row.source);
  return (
    <Tooltip>
      <TooltipTrigger asChild>{mark}</TooltipTrigger>
      {out === null ? (
        <TooltipContent side="top">{row.surface_label ?? word}</TooltipContent>
      ) : (
        <LinkedTip at={out} says={row.surface_label ?? word} />
      )}
    </Tooltip>
  );
}
