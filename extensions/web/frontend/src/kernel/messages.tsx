import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { IconCheck, IconChevronRight } from "@tabler/icons-react";

import {
  Attachment,
  AttachmentBadge,
  AttachmentContent,
  AttachmentDescription,
  AttachmentGroup,
  AttachmentThumbnail,
  AttachmentTitle,
  PickedThumbnail,
  attachmentBadgeFor,
} from "@/components/ui/attachment";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { buttonVariants } from "@/components/ui/button";
import {
  Item,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { FileSheet } from "@/kernel/artifact";
import { Lightbox } from "@/kernel/lightbox";
import { AgentIcon } from "@/lib/agentIcon";
import { BASE } from "@/lib/api";
import { agentName } from "@/lib/agentName";
import { speakerName } from "@/lib/audience";
import { BrandMark } from "@/lib/brandMark";
import { cn } from "@/lib/cn";
import { ConsentLink } from "@/lib/consent";
import type { EarlierMessages } from "@/lib/earlier";
import { Linked, Markdown, StreamingBody } from "@/lib/markdown";
import { modelLabel, modelMark } from "@/lib/models";
import { Moment, spanMoment } from "@/lib/moments";
import { agentHash } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { eventLabel, latestActivity } from "@/lib/turnStream";
import type { ActivityEvent, Bubble as Spoken, LiveTurn } from "@/lib/chatStore";
import type { ChatApp, ChatConnect, ChatFile, ChatQuestion, SubagentRun } from "@/lib/types";

/** How far from the foot still counts as being at it: judged to the pixel, a reply would stop following
 *  the moment a line landed or the composer grew a row. */
const AT_THE_FOOT_PX = 40;

/** The counter names whole seconds, so it is re-read at the rate its smallest part moves. */
const TICK_MS = 1_000;

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

function scrollerOf(node: Element): Element {
  for (let held = node.parentElement; held !== null; held = held.parentElement) {
    const overflow = getComputedStyle(held).overflowY;
    if (overflow === "auto" || overflow === "scroll") return held;
  }
  return document.scrollingElement ?? document.documentElement;
}

function placeOf(node: Element, scroller: Element): number {
  const top = node.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  return scroller === document.scrollingElement ? top : top + scroller.scrollTop;
}

export function OpenedAtTheFoot({ children }: { children: ReactNode }) {
  const held = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = held.current;
    if (node === null) return;
    const scroller = scrollerOf(node);
    scroller.scrollTop = scroller.scrollHeight;
  }, []);
  return <div ref={held}>{children}</div>;
}

function EarlierRow({ earlier }: { earlier: EarlierMessages }) {
  const row = useRef<HTMLDivElement>(null);
  const held = useRef<{ scroller: Element; anchor: Element; place: number } | null>(null);
  const load = () => {
    const node = row.current;
    if (node === null) return;
    const anchor = node.nextElementSibling;
    if (anchor !== null) {
      const scroller = scrollerOf(node);
      held.current = { scroller, anchor, place: placeOf(anchor, scroller) };
    }
    earlier.load();
  };
  const latest = useRef(load);
  latest.current = load;
  const watching = earlier.more && !earlier.loading && !earlier.failed;
  useEffect(() => {
    const node = row.current;
    if (!watching || node === null) return;
    const watcher = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) latest.current();
    });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [watching, earlier.pages.length]);
  useLayoutEffect(() => {
    const kept = held.current;
    if (kept === null) return;
    held.current = null;
    kept.scroller.scrollTop += placeOf(kept.anchor, kept.scroller) - kept.place;
  }, [earlier.pages.length]);
  if (earlier.failed) {
    return (
      <MessageScrollerItem ref={row}>
        <Marker render={<button type="button" onClick={load} />}>
          <MarkerContent>Couldn't load earlier messages — retry</MarkerContent>
        </Marker>
      </MessageScrollerItem>
    );
  }
  if (earlier.loading || earlier.more) {
    return (
      <MessageScrollerItem ref={row}>
        <Marker>
          <MarkerContent>{earlier.loading ? "Loading earlier messages…" : null}</MarkerContent>
        </Marker>
      </MessageScrollerItem>
    );
  }
  return <MessageScrollerItem ref={row} />;
}

export function MessageLog({
  messages,
  earlier,
  live = null,
  question,
  className,
  children,
  report = null,
}: {
  messages: Spoken[];
  earlier?: EarlierMessages;
  live?: LiveTurn | null;
  question?: (asked: ChatQuestion) => ReactNode;
  className?: string;
  children?: ReactNode;
  report?: string | null;
}) {
  const [opened, setOpened] = useState<Opened | null>(null);
  const openedReport = useRef<string | null>(null);
  useEffect(() => {
    if (!report || openedReport.current === report) return;
    const file = [...messages.flatMap((message) => message.files ?? []), ...(live?.files ?? [])].find(
      (candidate) => candidate.id === report,
    );
    if (!file) return;
    openedReport.current = report;
    setOpened({ files: [file], at: 0 });
  }, [report, messages, live]);
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
  const bubble = (message: Spoken, key: string) =>
    message.role === "error" ? (
      <MessageScrollerItem key={key} messageId={key}>
        <Meta>{message.text}</Meta>
      </MessageScrollerItem>
    ) : (
      <MessageScrollerItem key={key} messageId={key}>
        <Speech mine={message.role === "user"}>
          {message.role === "user" && message.speaker ? (
            <MessageHeader>{speakerName(message.speaker)}</MessageHeader>
          ) : null}
          {message.role === "user" ? (
            <Attached
              files={message.files ?? []}
              picked={message.attached ?? []}
              onOpen={setOpened}
            />
          ) : null}
          {message.role === "user" ? null : (
            <Activity
              events={message.events ?? []}
              runs={message.subagents ?? []}
              live={false}
              elapsed={message.elapsed}
            />
          )}
          {message.role === "user" && !message.text && !message.asked ? null : (
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
          )}
          {message.role === "user" || !message.files?.length ? null : (
            <Files files={message.files} onOpen={setOpened} />
          )}
          {message.role === "user" || !message.apps?.length ? null : <Apps apps={message.apps} />}
          {message.connect ? <ConnectLink connect={message.connect} /> : null}
          {message.meta ? <Meta model={message.model}>{message.meta}</Meta> : null}
          {message.question && question ? question(message.question) : null}
          <Stamp at={message.at} />
        </Speech>
      </MessageScrollerItem>
    );
  return (
    <>
      <MessageScrollerContent className={className} aria-busy={live !== null}>
        {earlier &&
        (earlier.pages.length > 0 || earlier.more || earlier.loading || earlier.failed) ? (
          <EarlierRow earlier={earlier} />
        ) : null}
        {earlier?.pages.flatMap((page) =>
          page.messages.map((said, at) => bubble(said, "h" + page.cursor + ":" + at)),
        )}
        {rows.map((row) =>
          row.live ? (
            <MessageScrollerItem key={"m" + String(row.at)} messageId={"m" + String(row.at)}>
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
                  <Files files={row.live.files} onOpen={setOpened} />
                ) : null}
                {row.live.apps.length ? <Apps apps={row.live.apps} /> : null}
                {row.live.connect ? <ConnectLink connect={row.live.connect} /> : null}
                <LiveMeter started={row.live.started} spend={row.live.meter} />
                {row.live.meta ? <Meta model={row.live.model}>{row.live.meta}</Meta> : null}
              </Speech>
            </MessageScrollerItem>
          ) : (
            bubble(row.said, "m" + String(row.at))
          ),
        )}
        {children}
      </MessageScrollerContent>
      {opened ? (
        <OpenedFile
          opened={opened}
          onMove={(at) => setOpened({ files: opened.files, at })}
          onClose={() => setOpened(null)}
        />
      ) : null}
    </>
  );
}

type Opened = { files: ChatFile[]; at: number };

type Row =
  | { at: number; said: Spoken; live?: undefined }
  | { at: number; said?: undefined; live: LiveTurn };

function Speech({ mine, children }: { mine: boolean; children: ReactNode }) {
  return (
    <Message align={mine ? "end" : "start"}>
      <MessageContent>{children}</MessageContent>
    </Message>
  );
}

/** When a message landed, under the words it belongs to: the smallest chrome the surface draws, so
 *  the transcript still reads as words rather than as a log. */
function Stamp({ at }: { at?: string }) {
  if (!at) return null;
  return (
    <div
      data-slot="message-stamp"
      className={cn(
        "px-lg text-small tabular-nums text-ink-soft",
        "group-has-data-[variant=ghost]/message:px-0",
      )}
    >
      <Moment at={at} />
    </div>
  );
}

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

const TAP_FLOOR = "max-narrow:inline-flex max-narrow:min-h-(--size-control) max-narrow:items-center";

function Files({ files, onOpen }: { files: ChatFile[]; onOpen: (opened: Opened) => void }) {
  const shared = files.filter((file) => file.role !== "details");
  const carried = files.filter((file) => file.role === "details");
  const images = shared.filter(
    (file) => file.media_type.startsWith("image/") && file.preview_url !== null,
  );
  const documents = shared.filter(
    (file) => !file.media_type.startsWith("image/") || file.preview_url === null,
  );
  return (
    <>
      {carried.map((file, index) => (
        <CarriedReport
          key={file.filename + String(index)}
          file={file}
          onOpen={() => onOpen({ files: [file], at: 0 })}
        />
      ))}
      {images.length === 1 ? (
        <Picture file={images[0]} onOpen={() => onOpen({ files: images, at: 0 })} />
      ) : null}
      {images.length > 1 ? (
        <AttachmentGroup className="mt-2xs items-start">
          {images.map((file, index) => (
            <Picture
              key={file.filename + String(index)}
              file={file}
              onOpen={() => onOpen({ files: images, at: index })}
              grouped
            />
          ))}
        </AttachmentGroup>
      ) : null}
      {documents.length ? (
        <div
          data-slot="attachment-grid"
          className="mt-2xs grid grid-cols-2 items-start gap-lg max-narrow:grid-cols-1"
        >
          {documents.map((file, index) => (
            <FileCard
              key={file.filename + String(index)}
              file={file}
              onOpen={() => onOpen({ files: [file], at: 0 })}
            />
          ))}
        </div>
      ) : null}
    </>
  );
}

function CarriedReport({ file, onOpen }: { file: ChatFile; onOpen: () => void }) {
  if (!file.url) {
    return <span className="mt-2xs font-mono text-small text-ink-soft">{file.filename}</span>;
  }
  return (
    <button
      type="button"
      onClick={onOpen}
      title={file.filename}
      className={cn(
        "mt-2xs cursor-pointer self-start border-0 bg-transparent p-0 text-left text-link underline",
        TAP_FLOOR,
      )}
    >
      {file.subject || "Open detailed report"}
    </button>
  );
}

function FileCard({ file, onOpen }: { file: ChatFile; onOpen: () => void }) {
  const thumbnail =
    file.preview_url === null ? null : (
      <AttachmentThumbnail filename={file.filename} previewUrl={file.preview_url} />
    );
  return (
    <Attachment size="sm" className={cn("w-full min-w-0", thumbnail && "flex-nowrap")}>
      {thumbnail === null ? null : (
        <button
          type="button"
          onClick={onOpen}
          aria-label={`Open ${file.filename}`}
          className="shrink-0 cursor-pointer border-0 bg-transparent p-0"
        >
          {thumbnail}
        </button>
      )}
      <AttachmentContent>
        <AttachmentTitle>
          <button
            type="button"
            onClick={onOpen}
            className={cn("cursor-pointer border-0 bg-transparent p-0 text-inherit", TAP_FLOOR)}
          >
            {file.filename}
          </button>
        </AttachmentTitle>
        {file.size_bytes === undefined ? null : (
          <AttachmentDescription>{formatSize(file.size_bytes)}</AttachmentDescription>
        )}
      </AttachmentContent>
    </Attachment>
  );
}

function Apps({ apps }: { apps: ChatApp[] }) {
  return (
    <ItemGroup className="mt-lg max-w-bubble">
      {apps.map((app, index) => (
        <Fragment key={app.id}>
          {index ? <ItemSeparator /> : null}
          <Item size="flush">
            <a
              href={agentHash(app.id)}
              className={cn(
                "flex min-w-0 flex-1 items-center gap-lg rounded-panel px-xl py-lg",
                "text-inherit no-underline hover:bg-fill",
              )}
            >
              <Avatar>
                <AvatarFallback>
                  <AgentIcon name={app.icon} />
                </AvatarFallback>
              </Avatar>
              <ItemContent>
                <ItemTitle>{agentName(app.name)}</ItemTitle>
                <ItemDescription>{app.model}</ItemDescription>
              </ItemContent>
              <IconChevronRight aria-hidden className="size-icon shrink-0 text-ink-soft" />
            </a>
          </Item>
        </Fragment>
      ))}
    </ItemGroup>
  );
}

function Attached({
  files,
  picked,
  onOpen,
}: {
  files: ChatFile[];
  picked: File[];
  onOpen: (opened: Opened) => void;
}) {
  if (!picked.length && !files.length) return null;
  return (
    <AttachmentGroup className="mb-2xs justify-end">
      {picked.length
        ? picked.map((file, at) => <PickedThumbnail key={file.name + String(at)} file={file} />)
        : files.map((file, at) => (
            <button
              key={file.filename}
              type="button"
              onClick={() => onOpen({ files, at })}
              aria-label={`Open ${file.filename}`}
              className="cursor-pointer border-0 bg-transparent p-0"
            >
              <AttachmentThumbnail filename={file.filename} previewUrl={file.preview_url} />
            </button>
          ))}
    </AttachmentGroup>
  );
}

function Picture({
  file,
  onOpen,
  grouped = false,
}: {
  file: ChatFile;
  onOpen: () => void;
  grouped?: boolean;
}) {
  const [failed, setFailed] = useState<string | null>(null);
  const badge = attachmentBadgeFor(file.filename);
  const className = cn(
    "w-fit",
    grouped ? "max-w-full shrink-0 snap-start" : "mt-2xs",
  );
  const drawn =
    failed === file.preview_url ? (
      <AttachmentThumbnail filename={file.filename} previewUrl={null} />
    ) : (
      <span className="relative block w-fit">
        <img
          loading="lazy"
          alt={file.filename}
          src={file.preview_url ?? undefined}
          onError={() => setFailed(file.preview_url)}
          className="max-h-(--media-card) max-w-full rounded-panel border border-edge object-contain"
        />
        {badge === null ? null : (
          <AttachmentBadge className="absolute bottom-0 left-0 m-sm">{badge}</AttachmentBadge>
        )}
      </span>
    );
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(className, "cursor-pointer border-0 bg-transparent p-0")}
    >
      {drawn}
    </button>
  );
}

function OpenedFile({
  opened,
  onMove,
  onClose,
}: {
  opened: Opened;
  onMove: (at: number) => void;
  onClose: () => void;
}) {
  const file = opened.files[opened.at];
  if (file.media_type.startsWith("image/") && file.preview_url !== null) {
    return (
      <Lightbox
        files={opened.files.map((held) => ({ ...held, subject: null }))}
        at={opened.at}
        onMove={onMove}
        onClose={onClose}
      />
    );
  }
  return <FileSheet file={{ ...file, subject: null }} onClose={onClose} />;
}

/** The turn's spend, with its model standing as the mark alone. The picker's name for that model
 *  reaches the member on hover and on focus. The mark is sized in `em` so it stands to the meta
 *  line's own type rather than to the glyph size a marker gives an icon. */
export function Meta({ model, children }: { model?: string | null; children: string }) {
  const mark = model ? modelMark(model) : null;
  return (
    <Marker className="mt-2xs font-mono text-small tabular-nums">
      {mark && model ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} className="flex shrink-0 items-center">
              <BrandMark provider={mark} className="size-icon" />
              <span className="sr-only">{modelLabel(model)}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent side="top">{modelLabel(model)}</TooltipContent>
        </Tooltip>
      ) : null}
      <MarkerContent>{children}</MarkerContent>
    </Marker>
  );
}

const THROB_DOTS = [
  { x: 5, y: 5, cell: 1 },
  { x: 12, y: 5, cell: 4 },
  { x: 19, y: 5, cell: 1, ahead: true },
  { x: 5, y: 12, cell: 2 },
  { x: 12, y: 12, cell: 5 },
  { x: 19, y: 12, cell: 2, ahead: true },
  { x: 5, y: 19, cell: 3 },
  { x: 12, y: 19, cell: 6 },
  { x: 19, y: 19, cell: 3, ahead: true },
];

function Throbber() {
  return (
    <svg
      aria-hidden
      data-throb
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      // A line box carries the room a descender needs, so its middle falls a pixel below the letters. The
      // pixel is taken back here — measured against the built sheet, the dots centre on the lowercase band.
      className="-translate-y-px shrink-0 animate-working motion-reduce:animate-none"
    >
      {THROB_DOTS.map((dot) => (
        <circle
          key={dot.x + "-" + dot.y}
          cx={dot.x}
          cy={dot.y}
          r={1}
          data-cell={dot.cell}
          data-ahead={dot.ahead === true ? "" : undefined}
        />
      ))}
    </svg>
  );
}

/** The turn's own clock, beside what it has spent so far, re-read every second while it runs. */
function LiveMeter({ started, spend }: { started: number; spend: string | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(tick);
  }, []);
  const run = spanMoment(now - started);
  return <Meta>{spend ? run + " · " + spend : run}</Meta>;
}

function Activity({
  events,
  runs,
  live,
  working,
  elapsed,
}: {
  events: ActivityEvent[];
  runs: SubagentRun[];
  live: boolean;
  working?: string;
  elapsed?: number;
}) {
  const [open, setOpen] = useState(false);
  const steps = events.length + runs.length;
  if (!steps && working === undefined) return null;
  const openRuns = runs.filter((run) => run.running).length;
  const summary = live
    ? openRuns > 0
      ? "Awaiting " + openRuns + " subagent" + (openRuns === 1 ? "" : "s")
      : (working ?? latestActivity(events, runs))
    : "Completed " +
      steps +
      " step" +
      (steps === 1 ? "" : "s") +
      (elapsed === undefined ? "" : " in " + spanMoment(elapsed));
  const shown = steps > 0 && open;
  return (
    <details
      className="group/activity mt-2xs text-label text-ink-soft"
      open={shown}
      // A browser fires `toggle` for the `open` this render writes as well as for a member's own click, so
      // only a state the fold was not already drawn in came from the member.
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
        {live ? <Throbber /> : null}
        <MarkerContent className={live ? "text-ink" : undefined}>{summary}</MarkerContent>
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

const AGENT_PROFILE = "agent:";

function RunRow({ run, live }: { run: SubagentRun; live: boolean }) {
  const [open, setOpen] = useState(false);
  const running = live && run.running === true;
  const current = running && run.current ? run.current : "";
  return (
    <li className="flex flex-col gap-hair">
      <details className="group/run" onToggle={(event) => setOpen(event.currentTarget.open)}>
        <summary className="flex cursor-pointer list-none items-center gap-xs">
          <span className="shrink-0">
            {run.name ||
              (run.profile.startsWith(AGENT_PROFILE)
                ? "App · " + agentName(run.profile.slice(AGENT_PROFILE.length))
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

function ConnectLink({ connect }: { connect: ChatConnect }) {
  const mark = connect.provider ? (
    <BrandMark provider={connect.provider} className="size-icon" />
  ) : null;
  const named = connect.label ?? "account";
  if (!connect.turn) {
    return (
      <span
        className={cn(
          buttonVariants({ variant: "outline", size: "bar" }),
          "self-start text-ink-soft",
        )}
      >
        {mark}
        {named + " connected"}
        {connect.account ? <span className="truncate">{connect.account}</span> : null}
        <IconCheck className="size-icon text-ink" aria-hidden />
      </span>
    );
  }
  // The address the press opens is this surface's own: it mints the consent URL for this turn's request
  // and redirects the window there, so the chip holds nothing that can go stale.
  return (
    <ConsentLink
      url={BASE + "/turns/" + connect.turn + "/connect"}
      className={cn(
        buttonVariants({ variant: "outline", size: "bar" }),
        "self-start no-underline",
      )}
    >
      {mark}
      {connect.label ? "Connect " + connect.label : "Connect account"}
    </ConsentLink>
  );
}
