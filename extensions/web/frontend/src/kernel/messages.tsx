import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import {
  IconCheck,
  IconChevronRight,
  IconCopy,
  IconFileText,
  IconWorld,
  IconX,
} from "@tabler/icons-react";

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
import { Button, buttonVariants } from "@/components/ui/button";
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
import { brailleOf, randomCell } from "@/lib/braille";
import { fullMoment, stampMoment } from "@/lib/moments";
import { agentHash } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { turnMeta } from "@/lib/turnMeta";
import {
  answerOf,
  latestActivity,
  layout,
  type Bubble as Spoken,
  type LiveTurn,
  type Row,
} from "@/lib/turnRecord";
import type {
  ChatApp,
  ChatConnect,
  ChatFile,
  ChatQuestion,
  SourceRef,
  SubagentRun,
} from "@/lib/types";

/** Judged to the pixel, the jump-to-foot button would flicker the moment a line landed or the
 *  composer grew a row. */
const AT_THE_FOOT_PX = 40;

const PREVIOUS_TURN_PEEK_PX = 88;

export function TranscriptScroll({ children }: { children: ReactNode }) {
  return (
    <MessageScrollerProvider
      defaultScrollPosition="last-anchor"
      scrollEdgeThreshold={AT_THE_FOOT_PX}
      scrollPreviousItemPeek={PREVIOUS_TURN_PEEK_PX}
    >
      {children}
    </MessageScrollerProvider>
  );
}

export function TranscriptPane({
  className,
  animate = false,
  children,
}: {
  className?: string;
  animate?: boolean;
  children: ReactNode;
}) {
  return (
    <MessageScroller className={className}>
      <MessageScrollerViewport data-testid="log" animate={animate}>
        {children}
      </MessageScrollerViewport>
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

/** A member's words are the characters they typed, except where they spoke over a surface that drew
 *  their emphasis as markup: those arrive as markdown and are drawn as the member saw them. */
function spoken(message: Spoken) {
  return message.markdown ? <Markdown text={message.text} /> : <Linked text={message.text} />;
}

const UFO_MARK = "ufo";
const SENT_BY_UFO = "Sent by UFO";

/** The member moving, in every way a browser reports it. A scroll is not among them: the mark
 *  scrolls the transcript itself, and a mark that cleared on its own scroll would never be seen. */
const MEMBER_ACTS = ["pointerdown", "keydown", "wheel", "touchstart"] as const;

/** The second accent, because the first is the fill a member's own words carry: a mark in that
 *  fill reads as a message the member sent. */
const MARKED =
  "rounded-panel bg-attention outline-2 outline-attention-ink" +
  " transition-[background-color,outline-color] duration-500";
const THROB = "animate-marked";
const LETTING_GO = "bg-transparent outline-transparent";

const MARK_HOLD_MS = 2200;
const MARK_FADE_MS = 500;

/** The mark is taken off `focus` and holds the row it first stood on, so a transcript settling
 *  under it neither moves it, restarts it, nor cuts it short. A member's act is sooner. */
function useMarkedTurn(
  focus: string | null,
  found: string | null,
): {
  letting: boolean;
  markedKey: string | null;
  land: (node: HTMLDivElement | null) => void;
} {
  const [marked, setMarked] = useState<string | null>(focus);
  const [letting, setLetting] = useState(false);
  const scrolled = useRef<string | null>(null);
  const landed = useRef<{ turn: string; key: string } | null>(null);
  if (marked !== null && found !== null && landed.current?.turn !== marked)
    landed.current = { turn: marked, key: found };
  const markedKey = landed.current?.turn === marked ? landed.current.key : null;
  useEffect(() => {
    setMarked(focus);
    setLetting(false);
  }, [focus]);
  useEffect(() => {
    if (marked === null) return;
    const clear = () => setMarked(null);
    for (const act of MEMBER_ACTS) window.addEventListener(act, clear, { passive: true });
    return () => {
      for (const act of MEMBER_ACTS) window.removeEventListener(act, clear);
    };
  }, [marked]);
  /** The hold starts when the mark lands on a row, not when the run reaches the address: a
   *  transcript still being read holds no row, and a mark that let go first would never be seen. */
  useEffect(() => {
    if (markedKey === null) return;
    const hold = window.setTimeout(() => setLetting(true), MARK_HOLD_MS);
    const gone = window.setTimeout(() => setMarked(null), MARK_HOLD_MS + MARK_FADE_MS);
    return () => {
      window.clearTimeout(hold);
      window.clearTimeout(gone);
    };
  }, [markedKey]);
  return {
    letting,
    markedKey,
    land: (node) => {
      if (node === null || marked === null || scrolled.current === marked) return;
      scrolled.current = marked;
      node.scrollIntoView({ block: "center" });
    },
  };
}

/** The row a run is marked on: the last reply the turn spoke, else the words that woke it. A
 *  source trigger's alert says no member is reading the run, so such a run often writes no reply. */
function markedRow(rows: Row[], turn: string | null): string | null {
  if (turn === null) return null;
  let reply: string | null = null;
  let woke: string | null = null;
  for (const row of rows) {
    if (row.kind !== "said" || row.message.turn !== turn) continue;
    if (row.message.role === "assistant") reply = row.key;
    else woke = row.key;
  }
  return reply ?? woke;
}

/** The key of a turn's own words on an earlier page, or null where no loaded page holds them. */
function earlierKey(earlier: EarlierMessages | undefined, turn: string): string | null {
  let reply: string | null = null;
  let woke: string | null = null;
  for (const page of earlier?.pages ?? [])
    page.messages.forEach((said, at) => {
      if (said.turn !== turn) return;
      const key = "h" + page.cursor + ":" + at;
      if (said.role === "assistant") reply = key;
      else woke = key;
    });
  return reply ?? woke;
}

export function MessageLog({
  messages,
  earlier,
  live = null,
  question,
  className,
  children,
  report = null,
  focus = null,
}: {
  messages: Spoken[];
  earlier?: EarlierMessages;
  live?: LiveTurn | null;
  question?: (asked: ChatQuestion) => ReactNode;
  className?: string;
  children?: ReactNode;
  report?: string | null;
  /** The turn a member arrived on, whose closing words are scrolled to and marked. A turn this
   *  transcript does not hold marks nothing. */
  focus?: string | null;
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
  const rows = layout(messages, live);
  const still = useReducedMotion();
  const spoke = markedRow(rows, focus);
  const { letting, markedKey, land } = useMarkedTurn(
    focus,
    focus === null ? null : spoke === null ? earlierKey(earlier, focus) : spoke,
  );
  const bubble = (message: Spoken, key: string, last = false) => {
    if (message.role === "error")
      return (
        <MessageScrollerItem key={key} messageId={key}>
          <Meta>{message.text}</Meta>
        </MessageScrollerItem>
      );
    const mine = message.role === "user";
    const answer = mine ? null : answerOf(message.text);
    const stamp = mine && message.at ? stampMoment(message.at) : null;
    const meta = mine
      ? stamp
        ? [stamp]
        : []
      : message.summary
        ? turnMeta(message.summary, message.at)
        : [];
    const standing = key === markedKey;
    return (
      <MessageScrollerItem
        key={key}
        messageId={key}
        scrollAnchor={mine}
        ref={standing ? land : undefined}
        data-highlight={standing || undefined}
        data-letting-go={(standing && letting) || undefined}
        data-hidden={message.hidden?.join(" ") || undefined}
        className={cn(
          standing && MARKED,
          standing && !letting && !still && THROB,
          standing && letting && LETTING_GO,
        )}
      >
        <Speech mine={mine} at={message.at}>
          {message.subagents?.some((run) => run.running) ? (
            <Activity working={null} runs={message.subagents} />
          ) : null}
          {message.role === "user" && message.fired ? (
            <MessageHeader className="gap-xs">
              <AgentIcon name={UFO_MARK} className="size-(--size-icon)" />
              {SENT_BY_UFO}
            </MessageHeader>
          ) : message.role === "user" && message.speaker ? (
            <MessageHeader>{speakerName(message.speaker)}</MessageHeader>
          ) : null}
          {message.role === "user" ? (
            <Attached
              files={message.files ?? []}
              picked={message.attached ?? []}
              onOpen={setOpened}
            />
          ) : null}
          {(message.role === "user" && !message.text && !message.asked) ||
          answer?.kind === "silence" ? null : (
            <Said mine={message.role === "user"}>
              {message.role === "user" && message.asked ? (
                <div className="text-small text-ink-soft">{message.asked}</div>
              ) : null}
              {message.role !== "user" ? (
                <Markdown text={message.text} />
              ) : message.arrival_id ? (
                <span className="italic text-ink-soft">{spoken(message)}</span>
              ) : message.fired?.provider ? (
                <span className="flex items-center gap-xs">
                  <BrandMark provider={message.fired.provider} className="size-icon" />
                  <span>{spoken(message)}</span>
                </span>
              ) : (
                spoken(message)
              )}
            </Said>
          )}
          {message.role === "user" || !message.files?.length ? null : (
            <Files files={message.files} onOpen={setOpened} />
          )}
          {message.role === "user" || !message.apps?.length ? null : <Apps apps={message.apps} />}
          {message.connect ? <ConnectLink connect={message.connect} /> : null}
          {meta.length ? (
            <Meta
              last={last}
              mine={mine}
              model={mine ? null : message.summary?.model}
              copy={mine ? message.text : answer?.kind === "words" ? answer.text : undefined}
            >
              {meta.map((part, index) => (
                <span key={index}>{part}</span>
              ))}
            </Meta>
          ) : null}
          {message.question && question ? question(message.question) : null}
        </Speech>
      </MessageScrollerItem>
    );
  };
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
        {rows.map((row, index) =>
          row.kind === "live" ? (
            <MessageScrollerItem
              key={row.key}
              messageId={row.key}
              data-folded={row.folded.length || undefined}
            >
              <Speech mine={false}>
                <Activity working={row.working} runs={row.waiting} sources={row.sources} />
                <Said mine={false} entering>
                  <StreamingBody text={row.body} />
                </Said>
                {row.record.files.length ? (
                  <Files files={row.record.files} onOpen={setOpened} />
                ) : null}
                {row.record.apps.length ? <Apps apps={row.record.apps} /> : null}
                {row.record.connect ? <ConnectLink connect={row.record.connect} /> : null}
              </Speech>
            </MessageScrollerItem>
          ) : (
            bubble(row.message, row.key, index === rows.length - 1)
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

/** The moment a message landed reaches the member under the pointer, never as a line of its own:
 *  a transcript reads as words rather than as a log. */
function Speech({ mine, at, children }: { mine: boolean; at?: string; children: ReactNode }) {
  return (
    <Message align={mine ? "end" : "start"}>
      <MessageContent title={at ? fullMoment(at) : undefined}>{children}</MessageContent>
    </Message>
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
      className={cn(
        mine ? "self-end *:data-[slot=bubble-content]:bg-said" : "w-full",
        entering && "animate-appear",
      )}
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
 *  line's own type rather than to the glyph size a marker gives an icon.
 *
 *  Only the last message in a transcript carries its line openly. An earlier one draws it on hover
 *  and on keyboard focus, so a long thread reads as speech rather than as a ledger. The line keeps
 *  its space either way: revealing it must not move the text above it. */
export function Meta({
  model,
  last = true,
  mine = false,
  copy,
  children,
}: {
  model?: string | null;
  last?: boolean;
  mine?: boolean;
  copy?: string;
  children: ReactNode;
}) {
  const mark = model ? modelMark(model) : null;
  return (
    <Marker
      className={cn(
        "mt-2xs gap-md text-small leading-none tabular-nums",
        "transition-opacity motion-reduce:transition-none",
        mine && "justify-end text-right",
        !last && "opacity-0 group-hover/message:opacity-100 group-focus-within/message:opacity-100",
      )}
    >
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
      {copy ? <CopySaid text={copy} /> : null}
      <MarkerContent className="flex items-center gap-md">{children}</MarkerContent>
    </Marker>
  );
}

const COPY_LABELS = {
  idle: "Copy message",
  copied: "Copied",
  failed: "Copy failed",
} as const;

const COPIED_MS = 2_000;

/** A browser refuses `writeText` when the document is not focused or the permission is withheld,
 *  and a rejection left unhandled would draw nothing at all. */
function CopySaid({ text }: { text: string }) {
  const [state, setState] = useState<keyof typeof COPY_LABELS>("idle");
  const fades = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (fades.current) window.clearTimeout(fades.current);
    },
    [],
  );
  const copy = async () => {
    let next: keyof typeof COPY_LABELS = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setState(next);
    if (fades.current) window.clearTimeout(fades.current);
    fades.current = window.setTimeout(() => setState("idle"), COPIED_MS);
  };
  return (
    <Button
      variant="mark"
      size="glyph"
      aria-label={COPY_LABELS[state]}
      className={cn(
        "size-(--size-icon) [&_svg]:size-(--size-icon) text-ink-soft",
        TAP_FLOOR,
        "max-narrow:min-w-(--size-control) max-narrow:justify-center",
        state === "failed" && "text-attention-ink",
      )}
      onClick={copy}
    >
      {state === "copied" ? (
        <IconCheck stroke={1.5} aria-hidden />
      ) : state === "failed" ? (
        <IconX stroke={1.5} aria-hidden />
      ) : (
        <IconCopy stroke={1.5} aria-hidden />
      )}
    </Button>
  );
}

/** The mark surfaces in the static oftener than the rest of the pool, so the shape a member already
 *  reads as ufo keeps coming back out of the noise. */
const ACCENTS = Array.from("∴∵∷⁘⁙⋮⋰⋱∧∨⊻⊼△▽◁▷⊳⊲⊙⊚⊛⊕⌾");
const ACCENT_MARK = "∵";
const ACCENT_MARK_WEIGHT = 2.5;
const ACCENT_SHARE = 0.14;

/** The decode splits by fraction of its own length: ciphertext, then a churn the accents drop out
 *  of, then a resolve front travelling left to right. */
const DECODE_MS = 1_500;
const HOLD_MS = 1_200;
const FRAME_MS = 90;
const STATIC_END = 0.35;
const CHURN_END = 0.7;

/** A wait that changes snaps to the new ciphertext and reads as a cut. Entering at the churn takes
 *  the old words apart and builds the new ones out of the same noise. */
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
 *  right — the wait a message states, in place of the `Loading` mark or a skeleton rather than
 *  beside one.
 *
 *  The decode runs once, when the wait changes. A step that stays put has already been read, so the
 *  resolved words shimmer instead of churning again — the movement says the step is still running
 *  without asking to be re-read. `loop` restores the repeating cycle; `delay` staggers a line
 *  against the ones above it; `color` drops the two channels for a dense or low-priority context,
 *  leaving the glyphs in the resting tone.
 *
 *  Reduced motion renders the words and starts no timer at all, and a hidden tab stops the one that
 *  is running: a line nobody is watching does not churn. */
export function DecodeLine({
  text,
  delay = 0,
  loop = false,
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

/** A column one character wide only holds glyphs a face gives one width to: in a proportional face
 *  a braille cell out of a fallback and an `m` beside it spill their columns and collide. */
function Cells({ cells, color }: { cells: DecodeCell[]; color: boolean }) {
  const words: { cell: DecodeCell; at: number }[][] = [[]];
  cells.forEach((cell, at) => {
    if (cell.glyph === " ") words.push([]);
    else words[words.length - 1].push({ cell, at });
  });
  return (
    <span aria-hidden data-slot="decode-text" className="font-mono text-small">
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

const AGENT_PROFILE = "agent:";

function runStep(run: SubagentRun): string {
  if (run.current) return run.current;
  const deeper = latestActivity(run.events, run.subagents);
  if (deeper) return deeper;
  if (run.name) return run.name;
  return run.profile.startsWith(AGENT_PROFILE)
    ? "App · " + agentName(run.profile.slice(AGENT_PROFILE.length))
    : "Subagent · " + run.profile;
}

const FAVICON_SERVICE = "https://www.google.com/s2/favicons";
const FAVICON_SIZE = 32;
const SOURCE_TILES_PER_ROW = 8;

/** The favicon service answers by host, so an address that names none draws the globe instead. */
export function faviconUrl(url: string): string | null {
  if (!URL.canParse(url)) return null;
  const host = new URL(url).hostname;
  return host ? `${FAVICON_SERVICE}?domain=${encodeURIComponent(host)}&sz=${FAVICON_SIZE}` : null;
}

/** One place the turn read, as a tile: the site's favicon, or a workspace page's provider mark.
 *  The title is the tooltip and the accessible name; a web tile opens its address. */
function SourceTile({ source }: { source: SourceRef }) {
  const [broken, setBroken] = useState(false);
  const favicon = source.kind === "web" && !broken ? faviconUrl(source.url ?? "") : null;
  const Glyph = source.kind === "web" ? IconWorld : IconFileText;
  const drawn = favicon ? (
    <img
      src={favicon}
      alt=""
      onError={() => setBroken(true)}
      className="block size-(--size-site-icon) rounded-site-icon"
    />
  ) : source.kind === "workspace" && source.provider ? (
    <BrandMark provider={source.provider} className="size-(--size-site-icon)" />
  ) : (
    <Glyph className="size-(--size-site-icon) text-ink-soft" aria-hidden />
  );
  const tile = (
    <span
      data-slot="source-tile"
      title={source.title}
      className="grid size-(--size-site-tile) shrink-0 place-items-center rounded-key bg-tile"
    >
      {drawn}
      <span className="sr-only">{source.title}</span>
    </span>
  );
  if (!source.url) return tile;
  return (
    <a href={source.url} target="_blank" rel="noreferrer" className="no-underline">
      {tile}
    </a>
  );
}

/** A web tile stands for its site, so two pages of one host share the first page's tile; every
 *  other kind is one tile per record. */
function tiled(items: SourceRef[]): SourceRef[] {
  const seen = new Set<string>();
  return items.filter((source) => {
    const url = source.url ?? "";
    const host = URL.canParse(url) ? new URL(url).hostname : url;
    const key = source.kind === "web" ? host : (source.ref ?? "");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** What the turn has read so far, one row: a tile per site or record, then how many. */
function Consulted({ sources }: { sources: SourceRef[] }) {
  if (sources.length === 0) return null;
  return (
    <Marker data-slot="sources" className="mt-2xs">
      <span className="flex items-center gap-hair">
        {tiled(sources)
          .slice(0, SOURCE_TILES_PER_ROW)
          .map((source) => <SourceTile key={source.url || source.ref} source={source} />)}
      </span>
      <MarkerContent>{sources.length + (sources.length === 1 ? " source" : " sources")}</MarkerContent>
    </Marker>
  );
}

/** The step a turn is on, what it has read, then the step of each subagent it waits on: a count
 *  alone reads as a stuck turn. A turn holding no running run draws no line — its reply is what it did. */
function Activity({
  working,
  runs,
  sources = [],
}: {
  working: string | null;
  runs: SubagentRun[];
  sources?: SourceRef[];
}) {
  const waiting = runs.filter((run) => run.running);
  const step =
    waiting.length > 0
      ? "Awaiting " + waiting.length + " subagent" + (waiting.length === 1 ? "" : "s")
      : working;
  if (!step && sources.length === 0) return null;
  return (
    <>
      {step ? (
        <Marker className="mt-2xs text-label text-ink-soft">
          <MarkerContent className="shimmer">
            <DecodeLine text={step} />
          </MarkerContent>
        </Marker>
      ) : null}
      <Consulted sources={sources} />
      {waiting.map((run) => (
        <Marker
          key={run.turn_id ?? run.conversation_id}
          className="mt-hair pl-2xl text-label text-ink-soft"
        >
          <MarkerContent className="shimmer truncate">
            <DecodeLine text={runStep(run)} />
          </MarkerContent>
        </Marker>
      ))}
    </>
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
