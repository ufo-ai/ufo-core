import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";

import { IconChevronRight } from "@tabler/icons-react";

import {
  Attachment,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentTitle,
} from "@/components/ui/attachment";
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
import { brailleOf, randomCell } from "@/lib/braille";
import { cn } from "@/lib/cn";
import { Linked, Markdown, StreamingBody } from "@/lib/markdown";
import { formatSize } from "@/lib/size";
import { eventLabel, latestActivity } from "@/lib/turnStream";
import type { ActivityEvent, Bubble as Spoken, LiveTurn } from "@/lib/chatStore";
import type { ChatFile, ChatQuestion, SubagentRun } from "@/lib/types";

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
 *  A reply that shared files draws them under its words: a picture as the picture it is, every
 *  other file as a card. `onOpenArtifacts` is where pressing a picture goes on a screen with an
 *  artifacts sidebar; a pane without one passes none and the picture links to the file itself.
 *
 *  `children` is what the screen hangs at the foot of the transcript, in the same column — a
 *  handoff, an empty state — so nothing floats over the conversation in a pane of its own. */
export function MessageLog({
  messages,
  live = null,
  question,
  onOpenArtifacts,
  className,
  children,
}: {
  messages: Spoken[];
  live?: LiveTurn | null;
  question?: (asked: ChatQuestion) => ReactNode;
  onOpenArtifacts?: () => void;
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
            <Activity events={message.events ?? []} runs={message.subagents ?? []} live={false} />
          )}
          <Said mine={message.role === "user"}>
            {message.role === "user" && message.asked ? (
              <div className="text-small text-ink-soft">{message.asked}</div>
            ) : null}
            {message.role !== "user" ? (
              <Markdown text={message.text} />
            ) : message.arrival_id ? (
              <span className="italic text-ink-soft">
                <Linked text={message.text} />
              </span>
            ) : (
              <Linked text={message.text} />
            )}
          </Said>
          {message.files?.length ? (
            <Files files={message.files} onOpen={onOpenArtifacts} />
          ) : null}
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
              {row.live.files.length ? (
                <Files files={row.live.files} onOpen={onOpenArtifacts} />
              ) : null}
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

const MARKDOWN_MEDIA_TYPE = "text/markdown";

/** What a reply's turn shared, under the words that shared it. A file that is itself a picture is
 *  drawn as one — part of the answer, and pressing it goes to `onOpen` where the screen has an
 *  artifacts sidebar; every other file is a card on a row that scrolls sideways rather than a
 *  column that pushes the rest of the conversation down the page. A markdown card is a document
 *  the sidebar draws, so on a screen with one it opens there too; everywhere else a card links to
 *  the file itself. */
function Files({ files, onOpen }: { files: ChatFile[]; onOpen?: () => void }) {
  const pictures = files.filter((file) => file.preview_url);
  const cards = files.filter((file) => !file.preview_url);
  return (
    <>
      {pictures.map((file) => (
        <Picture key={file.filename} file={file} onOpen={onOpen} />
      ))}
      {cards.length ? (
        <AttachmentGroup className="mt-2xs">
          {cards.map((file) => (
            <Attachment key={file.filename} size="sm">
              <AttachmentContent>
                <AttachmentTitle>
                  {onOpen && file.media_type === MARKDOWN_MEDIA_TYPE ? (
                    <button
                      type="button"
                      onClick={onOpen}
                      className="cursor-pointer border-0 bg-transparent p-0 text-inherit"
                    >
                      {file.filename}
                    </button>
                  ) : file.url ? (
                    <a href={file.url}>{file.filename}</a>
                  ) : (
                    file.filename
                  )}
                </AttachmentTitle>
                <AttachmentDescription>{formatSize(file.size_bytes)}</AttachmentDescription>
              </AttachmentContent>
            </Attachment>
          ))}
        </AttachmentGroup>
      ) : null}
    </>
  );
}

/** One shared picture, named by its filename. A pane with no sidebar to open draws it as a link to
 *  the file itself. */
function Picture({ file, onOpen }: { file: ChatFile; onOpen?: () => void }) {
  const drawn = (
    <img
      loading="lazy"
      alt={file.filename}
      src={file.preview_url ?? undefined}
      className="max-h-(--media-card) max-w-full rounded-panel border border-edge object-contain"
    />
  );
  if (onOpen) {
    return (
      <button
        type="button"
        onClick={onOpen}
        className="mt-2xs w-fit cursor-pointer border-0 bg-transparent p-0"
      >
        {drawn}
      </button>
    );
  }
  if (file.url) {
    return (
      <a href={file.url} className="mt-2xs w-fit">
        {drawn}
      </a>
    );
  }
  return <div className="mt-2xs w-fit">{drawn}</div>;
}

export function Meta({ children }: { children: ReactNode }) {
  return (
    <Marker className="mt-2xs font-mono text-small tabular-nums">
      <MarkerContent>{children}</MarkerContent>
    </Marker>
  );
}

/** The reply's activity disclosure, and while the turn runs the one line saying what the agent is
 *  doing. Live it leads with what is happening — `Awaiting N subagents` while runs are open, else
 *  the current step — and stands open while the turn runs, so the member reads the thoughts and the
 *  calls where they happened; settled it collapses to `Completed N steps`: the reply's own steps, a
 *  run counting as one however much it did inside. A member's own toggle wins over the default from
 *  then on.
 *
 *  A turn opens on this line with nothing yet behind it, and the first tool call arrives into the
 *  same line rather than replacing it. That is why the disclosure is drawn whether or not it has
 *  steps to disclose: swapping the element the moment the first step lands would take the mark and
 *  the words down with it, restarting an orbit mid-turn and cutting the glyphing short. With
 *  nothing behind it the line carries no chevron and does not open.
 *
 *  It is not a live region: the log it stands in already announces what is added to it, and a
 *  marker that replaced its own words on every tool call would read the whole run of a turn out
 *  over whatever else the page had to say. */
function Activity({
  events,
  runs,
  live,
  working,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  live: boolean;
  working?: string;
}) {
  const [open, setOpen] = useState<boolean | null>(null);
  const steps = events.length + runs.length;
  if (!steps && working === undefined) return null;
  const openRuns = runs.filter((run) => run.running).length;
  const summary = live
    ? openRuns > 0
      ? "Awaiting " + openRuns + " subagent" + (openRuns === 1 ? "" : "s")
      : (working ?? latestActivity(events, runs))
    : "Completed " + steps + " step" + (steps === 1 ? "" : "s");
  const shown = steps > 0 && (open ?? live);
  return (
    <details
      className="group/activity mt-2xs font-mono text-small text-ink-soft"
      open={shown}
      // A browser fires `toggle` for the `open` this render writes as well as for a member's own
      // click, so only a state the fold was not already drawn in came from the member. Recording
      // the render's own open would read as a member holding the fold open, and the reply would
      // never roll up to `Completed N steps` once the turn settled.
      onToggle={(event) => {
        if (event.currentTarget.open !== shown) setOpen(event.currentTarget.open);
      }}
    >
      <Marker
        render={
          <summary
            className={cn("list-none", steps ? "cursor-pointer" : "cursor-default")}
            onClick={steps ? undefined : (event) => event.preventDefault()}
          />
        }
      >
        {live ? <OrbitMark /> : null}
        <MarkerContent>{live ? <DecodeLine text={summary} /> : summary}</MarkerContent>
        {steps ? (
          <IconChevronRight
            aria-hidden
            className="size-icon shrink-0 transition-transform group-open/activity:rotate-90"
          />
        ) : null}
      </Marker>
      {shown ? <ActivityTree events={events} runs={runs} live={live} /> : null}
    </details>
  );
}

/** The reply's work in order. Rows interleave where their run started (`at`); a durable run
 *  carries no slot and stands after the events. */
function ActivityTree({
  events,
  runs,
  live,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  live: boolean;
}) {
  if (!events.length && !runs.length) return null;
  const slot = (run: SubagentRun) =>
    run.at === undefined || run.at > events.length ? events.length : run.at;
  const rows: ReactNode[] = [];
  const place = (index: number) => {
    for (const run of runs) {
      if (slot(run) === index) {
        rows.push(<RunRow key={run.conversation_id} run={run} live={live} />);
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
function RunRow({ run, live }: { run: SubagentRun; live: boolean }) {
  const [open, setOpen] = useState(false);
  const running = live && run.running === true;
  const current = running && run.current ? run.current : "";
  return (
    <li className="flex flex-col gap-hair">
      <details className="group/run" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="flex cursor-pointer list-none items-center gap-sm">
          <span className="shrink-0">
            {run.name ||
              (run.profile.startsWith("agent:")
                ? "Agent · " + run.profile.slice("agent:".length)
                : "Subagent · " + run.profile)}
          </span>
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
            <ActivityTree events={run.events} runs={run.subagents} live={live} />
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

/** The mark the portal draws while the agent is working: three lights turning on one ellipse seen
 *  nearly edge-on. They spread out of the ∵ triangle, gather back onto its vertices, hold a beat,
 *  and set off again; under reduced motion they sit parked on the triangle. `theme.css` holds the
 *  motion, the palette composites, and `--s`, the one length every dimension is a fraction of. */
export function OrbitMark({ className }: { className?: string }) {
  return (
    <span aria-hidden data-slot="orbit" className={cn("shrink-0", className)}>
      <span />
      <span />
      <span />
    </span>
  );
}

/** The mark surfaces in the static oftener than the rest of the pool, so the shape a member already
 *  reads as ufo keeps coming back out of the noise. */
const ACCENTS = Array.from("∴∵∷⁘⁙⋮⋰⋱∧∨⊻⊼△▽◁▷⊳⊲⊙⊚⊛⊕⌾");
const ACCENT_MARK = "∵";
const ACCENT_MARK_WEIGHT = 2.5;
const ACCENT_SHARE = 0.14;

/** One cycle: the decode, then the line held in plain sight before it runs again. The decode splits
 *  by fraction of its own length — ciphertext, then a churn the accents drop out of, then a resolve
 *  front travelling left to right. */
const DECODE_MS = 1_500;
const HOLD_MS = 1_200;
const FRAME_MS = 90;
const STATIC_END = 0.35;
const CHURN_END = 0.7;

/** Where a line restarts when the wait it names changes under it. A turn goes from `Thinking…` to
 *  its first tool step without the marker moving, and snapping straight to the new message's
 *  ciphertext reads as a cut. Entering at the churn instead takes the old words apart and builds the
 *  new ones out of the same noise, which is the one moment the effect has to carry. */
const CHURN_FRAME = Math.ceil((STATIC_END * DECODE_MS) / FRAME_MS);

/** The accents breathe on their own period, offset per cell so the line shimmers rather than blinks
 *  in unison; every other unresolved cell takes a slow wave travelling along the line. */
const PULSE_PERIOD_S = 2.6;
const PULSE_CELL_PHASE = 0.11;
const RIPPLE_CELLS_PER_WAVE = 7;

export type DecodeCell = {
  glyph: string;
  resolved: boolean;
  accent: boolean;
  /** How far along its channel this cell sits, 0 to 100, for `color-mix`. */
  mix: number;
};

/** The glyph a cell shows whenever it is not churning, and whether it is one of the ember few. */
export type SettledCell = { glyph: string; accent: boolean };

function weightedAccent(): string {
  const total = ACCENTS.length - 1 + ACCENT_MARK_WEIGHT;
  let ticket = Math.random() * total;
  for (const accent of ACCENTS) {
    ticket -= accent === ACCENT_MARK ? ACCENT_MARK_WEIGHT : 1;
    if (ticket < 0) return accent;
  }
  return ACCENT_MARK;
}

/** The line every phase but the churn draws, settled once: which cells carry an accent and which
 *  accent each carries, and the one cell a character with no braille of its own — a digit, a
 *  bracket — stands behind. Rolling either per frame would leave the static phase twitching and
 *  would multiply the ember. A space is never an accent, and never anything but itself. */
export function settle(text: string): SettledCell[] {
  const places = Array.from(text, (character, index) => (character === " " ? -1 : index)).filter(
    (index) => index !== -1,
  );
  const wanted = Math.round(places.length * ACCENT_SHARE);
  const chosen = new Set<number>();
  while (chosen.size < wanted && chosen.size < places.length) {
    chosen.add(places[Math.floor(Math.random() * places.length)]);
  }
  return Array.from(text, (character, index) => {
    if (character === " ") return { glyph: character, accent: false };
    if (chosen.has(index)) return { glyph: weightedAccent(), accent: true };
    return { glyph: brailleOf(character) ?? randomCell(), accent: false };
  });
}

function pulseMix(index: number, now: number): number {
  return ((Math.sin((now * 2 * Math.PI) / PULSE_PERIOD_S + index * PULSE_CELL_PHASE) + 1) / 2) * 100;
}

function rippleMix(index: number, now: number): number {
  return (
    ((Math.sin((index / RIPPLE_CELLS_PER_WAVE - now) * 2 * Math.PI) + 1) / 2) * 100
  );
}

/** The line as it stands at progress `t` through the decode, on a clock of `now` seconds. Pure: the
 *  same arguments draw the same line, so the phases can be asserted without a timer. */
export function decodeFrame(
  text: string,
  t: number,
  now: number,
  settled: SettledCell[],
): DecodeCell[] {
  const characters = Array.from(text);
  const churning = t >= STATIC_END && t < CHURN_END;
  const resolved =
    t >= CHURN_END ? Math.floor(((t - CHURN_END) / (1 - CHURN_END)) * characters.length) : 0;
  return characters.map((character, index) => {
    if (character === " ") return { glyph: character, resolved: true, accent: false, mix: 0 };
    if (index < resolved) return { glyph: character, resolved: true, accent: false, mix: 0 };
    if (churning) {
      return { glyph: randomCell(), resolved: false, accent: false, mix: rippleMix(index, now) };
    }
    const cell = settled[index];
    return {
      glyph: cell.glyph,
      resolved: false,
      accent: cell.accent,
      mix: cell.accent ? pulseMix(index, now) : rippleMix(index, now),
    };
  });
}

/** A wait, spelled. The label arrives as the braille that spells it, churns, and resolves left to
 *  right — the portal's one indeterminate loading state, in place of a spinner or a skeleton rather
 *  than beside one.
 *
 *  `delay` staggers a line against the ones above it. `loop` runs the cycle until the wait ends; a
 *  wait with a known end takes `loop={false}` and decodes once. `color` drops the two channels for a
 *  dense or low-priority context, leaving the glyphs in the resting tone.
 *
 *  Reduced motion renders the words and starts no timer at all, and a hidden tab stops the one that
 *  is running: a line nobody is watching does not churn. */
export function DecodeLine({
  text,
  delay = 0,
  loop = true,
  color = true,
  className,
}: {
  text: string;
  delay?: number;
  loop?: boolean;
  color?: boolean;
  className?: string;
}) {
  const still = useReducedMotion();
  const settled = useMemo(() => settle(text), [text]);
  const [shown, setShown] = useState(text);
  const [tick, setTick] = useState(0);
  const frames = useRef(0);

  /** A new wait glyphs in from the churn rather than inheriting how far the one before it had got,
   *  which would show a stranger's words already half resolved. */
  if (shown !== text) {
    setShown(text);
    setTick(CHURN_FRAME);
    frames.current = CHURN_FRAME;
  }

  useEffect(() => {
    if (still) return;
    let timer: number | undefined;
    let spent = false;
    const advance = () => {
      frames.current += 1;
      if (!loop && frames.current * FRAME_MS >= DECODE_MS) {
        spent = true;
        window.clearInterval(timer);
      }
      setTick(frames.current);
    };
    const start = () => {
      window.clearInterval(timer);
      if (!spent) timer = window.setInterval(advance, FRAME_MS);
    };
    const opening = window.setTimeout(start, delay);
    const watch = () => (document.hidden ? window.clearInterval(timer) : start());
    document.addEventListener("visibilitychange", watch);
    return () => {
      window.clearTimeout(opening);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", watch);
    };
  }, [still, text, delay, loop]);

  if (still) return <span className={className}>{text}</span>;

  const elapsed = tick * FRAME_MS;
  const t = Math.min(1, (loop ? elapsed % (DECODE_MS + HOLD_MS) : elapsed) / DECODE_MS);
  return (
    <span className={className}>
      <span className="sr-only">{text}</span>
      <Cells cells={decodeFrame(text, t, elapsed / 1_000, settled)} color={color} />
    </span>
  );
}

/** Cells are pinned to one column and grouped by word, so the line breaks where the words do and the
 *  box a member reads is the same box before and after the resolve. */
function Cells({ cells, color }: { cells: DecodeCell[]; color: boolean }) {
  const words: { cell: DecodeCell; at: number }[][] = [[]];
  cells.forEach((cell, at) => {
    if (cell.glyph === " ") words.push([]);
    else words[words.length - 1].push({ cell, at });
  });
  return (
    <span aria-hidden data-slot="decode-text">
      {words.map((word, index) => (
        <span key={index}>
          {index > 0 ? " " : null}
          <span className="inline-block whitespace-pre">
            {word.map(({ cell, at }) => (
              <span
                key={at}
                data-slot="decode-cell"
                data-resolved={cell.resolved ? "" : undefined}
                data-accent={cell.accent ? "" : undefined}
                style={
                  color && !cell.resolved
                    ? ({ "--f": cell.mix.toFixed(1) } as CSSProperties)
                    : undefined
                }
                className={cn(
                  "inline-block w-(--size-decode-cell) text-center",
                  cell.resolved
                    ? "font-medium text-foreground"
                    : color
                      ? cell.accent
                        ? "decode-pulse"
                        : "decode-ripple"
                      : "text-muted-foreground",
                )}
              >
                {cell.glyph}
              </span>
            ))}
          </span>
        </span>
      ))}
    </span>
  );
}

function useReducedMotion(): boolean {
  const [still, setStill] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const answer = () => setStill(query.matches);
    query.addEventListener("change", answer);
    return () => query.removeEventListener("change", answer);
  }, []);
  return still;
}
