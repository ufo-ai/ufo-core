import {
  IconArrowUpRight,
  IconBrandSlack,
  IconBrowser,
  IconHistoryToggle,
  IconLoader,
  IconLoader2,
  IconMessageCircle,
  IconTerminal2,
} from "@tabler/icons-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  IMESSAGE_SURFACE,
  SLACK_SURFACE,
  UFO_SURFACE,
  isPortalChat,
  slackLink,
  surfaceWord,
} from "@/lib/audience";
import { automationId } from "@/lib/automationLane";
import { cn } from "@/lib/cn";
import type { ChatRow, RailTurn } from "@/lib/rail";
import { automationsHash } from "@/lib/route";

export const AUTOMATION = "Automation";

const GLYPH = "size-(--size-glyph) shrink-0";

const MARK = "size-3.5 shrink-0";

const DOT = "size-sm rounded-full bg-current";

/** The picture a surface is drawn by, wherever the portal draws one. */
function ChannelGlyph({ surface, className }: { surface: string; className: string }) {
  if (surface === SLACK_SURFACE) return <IconBrandSlack className={className} aria-hidden />;
  if (surface === UFO_SURFACE) return <IconTerminal2 className={className} aria-hidden />;
  if (surface === IMESSAGE_SURFACE) return <IconMessageCircle className={className} aria-hidden />;
  return <IconBrowser className={className} aria-hidden />;
}

/** The portal's bullet, rather than its glyph, is what keeps a rail of mostly portal rows reading
 *  down one left edge; `rail.test.tsx` holds it. */
function Origin({ surface, automated, ink }: { surface: string; automated: boolean; ink: string }) {
  if (automated) return <IconHistoryToggle className={cn(MARK, ink)} aria-hidden />;
  if (isPortalChat(surface)) return <span aria-hidden className={cn(DOT, ink)} />;
  return <ChannelGlyph surface={surface} className={cn(MARK, ink)} />;
}

function Spinner({ glyph: Glyph, ink }: { glyph: typeof IconLoader; ink: string }) {
  return <Glyph className={cn(GLYPH, "animate-spin motion-reduce:animate-none", ink)} aria-hidden />;
}

/** A type predicate, so the ink table below stays total over the turns that reach it. */
function live(turn: RailTurn): turn is "running" | "queued" {
  return turn === "running" || turn === "queued";
}

const TURN_INK: Record<Exclude<RailTurn, "running" | "queued">, string> = {
  parked: "text-blocked",
  idle: "text-ink-quiet",
};

/** A running turn keeps its spinner instead: what the agent is doing right now outranks what
 *  stands unread above it. */
const UNREAD_INK = "text-live";

/** The rail has one glyph's width for what drove the chat, where it runs and whether it moved
 *  unread; the table draws those apart, off this same file, and states its own spacing. */
export function ChatMark({
  surface,
  turn,
  automated,
  unread,
  className,
}: {
  surface: string;
  turn: RailTurn;
  automated: boolean;
  unread: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      data-turn={turn}
      className={cn("flex items-center justify-center", GLYPH, className)}
    >
      {live(turn) ? (
        <Spinner glyph={IconLoader2} ink="text-primary" />
      ) : (
        <Origin surface={surface} automated={automated} ink={unread ? UNREAD_INK : TURN_INK[turn]} />
      )}
    </span>
  );
}

export type ChatState = "working" | "waiting" | "unread" | "idle";

/** What a chat is doing, in the order a member wants it: the agent is working, it is held for an
 *  answer only they can give, it moved while they were away, or it is resting. One reading feeds
 *  the mark and the order the table stands in, so the eye and the sort cannot disagree. */
export function chatState(row: ChatRow): ChatState {
  if (live(row.turn)) return "working";
  if (row.turn === "parked") return "waiting";
  return row.unread ? "unread" : "idle";
}

export const CHAT_STATE_RANK: Record<ChatState, number> = {
  working: 0,
  waiting: 1,
  unread: 2,
  idle: 3,
};

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

/** The table's status is the state alone and never the surface, which has a column of its own to
 *  be named in. Its spinner is grey where the rail's is primary: a column of them would otherwise
 *  read as a column of brand colour rather than one of status. */
export function ChatStatus({ row }: { row: ChatRow }) {
  const state = chatState(row);
  return (
    <span
      role="img"
      aria-label={STATE_WORDS[state]}
      data-state={state}
      className={cn("flex items-center justify-center", GLYPH)}
    >
      {state === "working" ? (
        <Spinner glyph={IconLoader} ink="text-ink-soft" />
      ) : (
        <span aria-hidden className={cn(DOT, STATE_INK[state])} />
      )}
    </span>
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

/** The mark names the automation that last fired here and leads to it: an automation runs on its
 *  own schedule, so what a member wants from its chat is usually the thing that scheduled it. */
export function AutomationMark({ row, className }: { row: ChatRow; className?: string }) {
  const name = row.automation_name;
  const kind = row.automation_kind;
  const mark = (
    <IconHistoryToggle
      className={cn(MARK, "text-ink-soft", className)}
      role="img"
      aria-label={AUTOMATION}
    />
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
function channelWord(surface: string): string {
  return isPortalChat(surface) ? "Web" : surfaceWord(surface);
}

/** The surface names itself in the column and the room it ran in stands under the pointer: one
 *  Slack workspace fills a column with `#`-prefixed names that differ only in their last word. The
 *  room leads back out to the thread where the surface reported a link for it. */
export function ChannelMark({ row }: { row: ChatRow }) {
  const cell = (
    <span className="flex min-w-0 items-center gap-2xs">
      <ChannelGlyph surface={row.surface} className={cn(MARK, "text-ink-soft")} />
      <span className="truncate">{channelWord(row.surface)}</span>
    </span>
  );
  if (!row.surface_label) return cell;
  const out = slackLink(row.surface, row.source);
  return (
    <Tooltip>
      <TooltipTrigger asChild>{cell}</TooltipTrigger>
      {out === null ? (
        <TooltipContent side="top">{row.surface_label}</TooltipContent>
      ) : (
        <LinkedTip at={out} says={row.surface_label} />
      )}
    </Tooltip>
  );
}
