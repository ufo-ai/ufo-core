import { useState, type ReactNode } from "react";

import { IconChevronRight } from "@tabler/icons-react";

import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent, MessageHeader } from "@/components/ui/message";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
  useMessageScroller,
} from "@/components/ui/message-scroller";
import { Reveal } from "@/components/ui/reveal";
import { speakerName } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { Markdown, StreamingBody } from "@/lib/markdown";
import { subagentConversationHash } from "@/lib/route";
import { eventLabel, latestActivity } from "@/lib/turnStream";
import type { ActivityEvent, Bubble as Spoken, LiveTurn } from "@/lib/chatStore";
import type { ChatQuestion, SubagentRun } from "@/lib/types";

/** How far from the foot still counts as being at it. A reader is at the bottom of a conversation
 *  long before they are at the last pixel of it: a line lands, the composer grows by a row, the
 *  pane settles a few pixels short. Judged to the pixel, a reply would stop following the moment
 *  any of that happened, and the member would be left reading a stream that had walked off the
 *  screen without them. */
const AT_THE_FOOT_PX = 40;

/** What every screen that draws a conversation stands in. It holds no element of its own, so it
 *  encloses the log and the acts beside it alike: a send or an answer taken outside the pane can
 *  still say "take me back to the foot", because a member who scrolled up to re-read something and
 *  then wrote is not asking to stay where they were — they have just added the newest thing on the
 *  page. `useTakeMeToTheFoot` is how those acts say it, and it reaches this through context, so a
 *  caller outside one is a mistake the primitive raises on rather than a silent no-scroll. */
export function TranscriptScroll({ children }: { children: ReactNode }) {
  return (
    <MessageScrollerProvider
      autoScroll
      defaultScrollPosition="end"
      scrollEdgeThreshold={AT_THE_FOOT_PX}
    >
      {children}
    </MessageScrollerProvider>
  );
}

/** The pane a conversation is scrolled in, with the way back to its foot standing over it. Only a
 *  screen that gives the conversation a height of its own draws one — the live chat, where the
 *  composer holds the bottom and the log takes what is left. A transcript read back stands in a page
 *  that already scrolls, and a second scroll region inside that page is a second scrollbar beside
 *  the same words. How the pane takes its height is the caller's to state: one that claimed a share
 *  of its container would collapse in a container with no height to share out, and clip the
 *  transcript to nothing.
 *
 *  The three things a transcript must not do — jump when a reply grows, jump when older messages
 *  load in above, scroll in front of a member who has scrolled away — are answered here once for
 *  every pane that draws one. */
export function TranscriptPane({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <MessageScroller className={className}>
      <MessageScrollerViewport data-testid="log">{children}</MessageScrollerViewport>
      <MessageScrollerButton />
    </MessageScroller>
  );
}

export function useTakeMeToTheFoot(): () => void {
  const { scrollToEnd } = useMessageScroller();
  return scrollToEnd;
}

/** One conversation's messages, as a column of rows. It draws no scroll region: a live chat hangs
 *  it in a `TranscriptPane`, a transcript read back hangs it straight in the page that scrolls it.
 *
 *  No message is a scroll anchor. Anchoring one would put the member's question at the top of the
 *  pane the moment they asked it and hold open the screenful of blank the pane needs to get it
 *  there — a reading position for a document, and this is not one. A conversation is read at its
 *  foot: the newest thing is the thing being said, and the pane follows it.
 *
 *  A member's words sit in a bubble at the end of the row; a reply is drawn with no surface at
 *  all, because it is a document in the reading column and not a card. Under each reply is what it
 *  did: the subagents it spawned, the tools it called, the account it asked to connect, what it
 *  cost. The live chat hands it the turn it is streaming; a transcript read back — an agent's
 *  conversations, a subagent's runs — hands it none and shows what landed. A conversation the
 *  member cannot reply to still reads exactly like the one they can.
 *
 *  The reply being written and the reply that landed are one row in one list, under the index the
 *  landed one will take. React rebuilds a row that moves between lists or changes key, and the pane
 *  follows the element rather than the name on it, so a row rebuilt at the end of every turn takes
 *  the transcript back to the top with it.
 *
 *  A member message a running turn has not taken up yet states that in its own words — italic and
 *  muted, no line added beneath them: a turn absorbs what arrived at its round boundaries, so the
 *  wait lasts as long as the call it is inside, and the words take their weight back when the turn
 *  says it took them up. Those messages are drawn last, under the reply streaming above them.
 *
 *  A bubble somebody other than the viewer spoke is headed by their name — the read hands it over
 *  only then, so the viewer's own bubbles stay the unlabelled default.
 *
 *  A question stands under the reply that asked it. The log decides the place and the view
 *  supplies the form: `question` draws one and a pane that cannot answer passes none.
 *
 *  `children` is what the screen hangs at the foot of the transcript, in the same column — a
 *  handoff, an empty state — so nothing floats over the conversation in a pane of its own. */
export function MessageLog({
  messages,
  live = null,
  conversationId,
  question,
  className,
  children,
}: {
  messages: Spoken[];
  live?: LiveTurn | null;
  conversationId: string | null;
  question?: (asked: ChatQuestion) => ReactNode;
  /** The reading column, set on the messages rather than on the pane that scrolls them: a pane
   *  narrowed to the column carries the scrollbar at the column's edge, which puts a moving bar
   *  in the middle of the screen beside the words instead of at the side of the window. */
  className?: string;
  children?: ReactNode;
}) {
  const waiting = messages.findIndex(
    (message) => message.arrival_id !== undefined || message.queued === true,
  );
  const settled = waiting === -1 ? messages : messages.slice(0, waiting);
  const queued = waiting === -1 ? [] : messages.slice(waiting);
  const rows: Row[] = [
    ...settled.map((said, at) => ({ at, said })),
    ...(live ? [{ at: settled.length, live }] : []),
    ...queued.map((said, index) => ({ at: settled.length + (live ? 1 : 0) + index, said })),
  ];
  const bubble = (message: Spoken, index: number) =>
    message.role === "error" ? (
      <MessageScrollerItem key={index} messageId={"m" + String(index)}>
        <Meta>{message.text}</Meta>
      </MessageScrollerItem>
    ) : (
      <MessageScrollerItem key={index} messageId={"m" + String(index)}>
        <Speech mine={message.role === "user"}>
          {message.role === "user" && message.speaker ? (
            <MessageHeader>{speakerName(message.speaker)}</MessageHeader>
          ) : null}
          {message.role === "user" ? null : (
            <Activity
              events={message.events ?? []}
              runs={message.subagents ?? []}
              root={conversationId}
              live={false}
            />
          )}
          <Said mine={message.role === "user"}>
            {message.role === "user" && message.asked ? (
              <div className="text-small text-ink-soft">{message.asked}</div>
            ) : null}
            {message.role !== "user" ? (
              <Markdown text={message.text} />
            ) : message.arrival_id ? (
              <span className="italic text-ink-soft">{message.text}</span>
            ) : (
              message.text
            )}
          </Said>
          {message.connectUrl ? <ConnectLink url={message.connectUrl} /> : null}
          {message.meta ? <Meta>{message.meta}</Meta> : null}
          {message.question && question ? question(message.question) : null}
        </Speech>
      </MessageScrollerItem>
    );
  return (
    <MessageScrollerContent className={className} aria-busy={live !== null}>
      {rows.map((row) =>
        row.live ? (
          <MessageScrollerItem key={row.at} messageId={"m" + String(row.at)}>
            <Speech mine={false}>
              <Activity
                events={row.live.events}
                runs={row.live.subagents}
                root={conversationId}
                live
                working={
                  row.live.reconnecting
                    ? "Reconnecting…"
                    : (row.live.activity ?? (row.live.text ? undefined : "Thinking…"))
                }
              />
              <Said mine={false} entering>
                <StreamingBody text={row.live.text} />
              </Said>
              {row.live.connectUrl ? <ConnectLink url={row.live.connectUrl} /> : null}
              {row.live.meter ? <Meta>{row.live.meter}</Meta> : null}
              {row.live.meta ? <Meta>{row.live.meta}</Meta> : null}
            </Speech>
          </MessageScrollerItem>
        ) : (
          bubble(row.said, row.at)
        ),
      )}
      {children}
    </MessageScrollerContent>
  );
}

type Row =
  | { at: number; said: Spoken; live?: undefined }
  | { at: number; said?: undefined; live: LiveTurn };

/** The row one message is said on, turned around for the member's own words. */
function Speech({ mine, children }: { mine: boolean; children: ReactNode }) {
  return (
    <Message align={mine ? "end" : "start"}>
      <MessageContent>{children}</MessageContent>
    </Message>
  );
}

/** The words themselves. The member's take the fill step and the reading width; a reply takes no
 *  surface and the whole column, which is the one difference between being quoted and being read. */
function Said({
  mine,
  entering = false,
  children,
}: {
  mine: boolean;
  entering?: boolean;
  children: ReactNode;
}) {
  return (
    <Bubble
      variant={mine ? "default" : "ghost"}
      align={mine ? "end" : "start"}
      data-role={mine ? "me" : "agent"}
      className={cn(mine ? "self-end" : "w-full", entering && "animate-appear")}
    >
      <BubbleContent
        className={cn("wrap-anywhere leading-reading [&_a]:text-link", mine && "whitespace-pre-wrap")}
      >
        {children}
      </BubbleContent>
    </Bubble>
  );
}

export function Meta({ children }: { children: ReactNode }) {
  return (
    <Marker className="mt-2xs font-mono text-small tabular-nums">
      <MarkerContent>{children}</MarkerContent>
    </Marker>
  );
}

/** What the agent is doing, said as a status line rather than as a bubble — it is about the
 *  conversation, not in it. It is not a live region: the log it stands in already announces what is
 *  added to it, and a marker that replaced its own words on every tool call would read the whole
 *  run of a turn out over whatever else the page had to say. */
function Working({ children }: { children: ReactNode }) {
  return (
    <Marker className="mt-2xs font-mono text-small">
      <MarkerContent className="shimmer">{children}</MarkerContent>
    </Marker>
  );
}

/** The reply's activity disclosure. Live it leads with what is happening — `Awaiting N subagents`
 *  while runs are open, else the current step — and opens itself the moment a run appears, so the
 *  member watches the rows work; settled it collapses to `Completed N steps`: the reply's own
 *  steps, a run counting as one however much it did inside. A member's own toggle wins over the
 *  default from then on. */
function Activity({
  events,
  runs,
  root,
  live,
  working,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  root: string | null;
  live: boolean;
  working?: string;
}) {
  const [open, setOpen] = useState<boolean | null>(null);
  if (!events.length && !runs.length) {
    return working === undefined ? null : <Working>{working}</Working>;
  }
  const openRuns = runs.filter((run) => run.running).length;
  const steps = events.length + runs.length;
  const summary = live
    ? openRuns > 0
      ? "Awaiting " + openRuns + " subagent" + (openRuns === 1 ? "" : "s")
      : (working ?? latestActivity(events, runs))
    : "Completed " + steps + " step" + (steps === 1 ? "" : "s");
  const shown = open ?? (live && openRuns > 0);
  return (
    <details
      className="group/activity mt-2xs font-mono text-small text-ink-soft"
      open={shown}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <Marker render={<summary className="cursor-pointer list-none" />}>
        <MarkerContent className={cn(live && "shimmer")}>{summary}</MarkerContent>
        <IconChevronRight
          aria-hidden
          className="size-icon shrink-0 transition-transform group-open/activity:rotate-90"
        />
      </Marker>
      {shown ? (
        <ActivityTree events={events} runs={runs} root={root} live={live} />
      ) : null}
    </details>
  );
}

/** A run's link is rooted at the conversation that spawned it: the one being read for a run under
 *  a reply, and the run's own for the runs it spawned in turn — which is the one generation the
 *  read behind that link authorizes against. Rows interleave where their run started (`at`);
 *  a durable run carries no slot and stands after the events. */
function ActivityTree({
  events,
  runs,
  root,
  live,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  root: string | null;
  live: boolean;
}) {
  if (!events.length && !runs.length) return null;
  const slot = (run: SubagentRun) =>
    run.at === undefined || run.at > events.length ? events.length : run.at;
  const rows: ReactNode[] = [];
  const place = (index: number) => {
    for (const run of runs) {
      if (slot(run) === index) {
        rows.push(
          <RunRow key={run.conversation_id} run={run} root={root} live={live} />,
        );
      }
    }
  };
  events.forEach((event, index) => {
    place(index);
    rows.push(
      <li key={"event-" + index} className="whitespace-pre-wrap">
        {event.kind === "note" ? (
          <Reveal bare>{event.text}</Reveal>
        ) : (
          eventLabel(event, "done")
        )}
      </li>,
    );
  });
  place(events.length);
  return (
    <ul className="m-0 mt-2xs flex list-none flex-col gap-hair p-0 pl-lg">
      {rows}
    </ul>
  );
}

/** One run's row: its name (the profile when the spawn gave none) linking to the run's own record,
 *  with what it is doing now beside it while it works; opening the row shows the work it has done,
 *  the runs it spawned in turn, and — once it answered — its answer. */
function RunRow({
  run,
  root,
  live,
}: {
  run: SubagentRun;
  root: string | null;
  live: boolean;
}) {
  const [open, setOpen] = useState(false);
  const running = live && run.running === true;
  const current = running && run.current ? run.current : "";
  return (
    <li className="flex flex-col gap-hair">
      <details className="group/run" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="flex cursor-pointer list-none items-center gap-sm">
          <a
            className="shrink-0"
            href={subagentConversationHash(
              run.profile,
              run.conversation_id,
              root ?? undefined,
            )}
          >
            {run.name || "Subagent · " + run.profile}
          </a>
          {current ? (
            <span className="shimmer min-w-0 truncate">{"· " + current}</span>
          ) : null}
          <IconChevronRight
            aria-hidden
            className="size-icon shrink-0 transition-transform group-open/run:rotate-90"
          />
        </summary>
        {open ? (
          <>
            <ActivityTree
              events={run.events}
              runs={run.subagents}
              root={run.conversation_id}
              live={live}
            />
            {run.output ? (
              <div className="pl-lg">
                <Reveal bare>
                  <Markdown text={run.output} />
                </Reveal>
              </div>
            ) : null}
          </>
        ) : null}
      </details>
    </li>
  );
}

function ConnectLink({ url }: { url: string }) {
  return (
    <a href={url} target="_blank" rel="noopener">
      Connect account
    </a>
  );
}

