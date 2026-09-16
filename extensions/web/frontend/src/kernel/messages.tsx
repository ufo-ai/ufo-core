import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { BubbleHeader } from "@/components/ui/bubble-header";
import { ConnectLink } from "@/components/ui/connect-link";
import { useReducedMotion } from "@/components/ui/decode";
import { EarlierRow, scrollerOf } from "@/components/ui/earlier-row";
import { Message, MessageContent, MessageHeader } from "@/components/ui/message";
import { Meta } from "@/components/ui/meta";
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
  useMessageScroller,
} from "@/components/ui/message-scroller";
import { TurnActivity } from "@/components/ui/turn-activity";
import { TurnApps } from "@/components/ui/turn-apps";
import { AttachedFiles, TurnFiles, type Opened } from "@/components/ui/turn-files";
import { FileSheet } from "@/kernel/artifact";
import { Lightbox } from "@/kernel/lightbox";
import { AgentIcon } from "@/lib/agentIcon";
import { BrandMark } from "@/lib/brandMark";
import { FaceCircle, photoAddress } from "@/lib/memberFace";
import type { EarlierMessages } from "@/lib/earlier";
import { Linked, Markdown, StreamingBody } from "@/lib/markdown";
import { fullMoment, stampMoment } from "@/lib/moments";
import { turnMeta } from "@/lib/turnMeta";
import {
  answerOf,
  layout,
  type Bubble as Spoken,
  type LiveTurn,
  type Row,
} from "@/lib/turnRecord";
import type { ChatQuestion, Speaker } from "@/lib/types";

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

/** The scroller finds the row a send added by counting the content's children and reads nothing
 *  past them, so what draws under the thread stands first among them and is laid out last. */
function Under({ children }: { children: ReactNode }) {
  return <div className="order-last mt-6xl flex flex-col gap-6xl empty:hidden">{children}</div>;
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
    const who: Who =
      message.role !== "user" ? "agent" : message.speaker ? "member" : "me";
    const mine = who === "me";
    const answer = who === "agent" ? answerOf(message.text) : null;
    const stamp = message.role === "user" && message.at ? stampMoment(message.at) : null;
    const meta =
      who === "agent"
        ? message.summary
          ? turnMeta(message.summary, message.at)
          : []
        : stamp
          ? [stamp]
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
        mark={standing ? (letting ? "letting-go" : "held") : undefined}
        throb={standing && !letting && !still}
      >
        <Speech mine={mine} at={message.at} speaker={who === "member" ? message.speaker : undefined}>
          {message.subagents?.some((run) => run.running) ? (
            <TurnActivity working={null} runs={message.subagents} />
          ) : null}
          {message.role === "user" && message.fired ? (
            <MessageHeader className="gap-xs">
              <AgentIcon name={UFO_MARK} className="size-(--size-icon)" />
              {SENT_BY_UFO}
            </MessageHeader>
          ) : null}
          {message.role === "user" ? (
            <AttachedFiles
              files={message.files ?? []}
              picked={message.attached ?? []}
              onOpen={setOpened}
            />
          ) : null}
          {(message.role === "user" && !message.text && !message.asked) ||
          answer?.kind === "silence" ? null : (
            <Said who={who} speaker={who === "member" ? message.speaker : undefined}>
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
            <TurnFiles files={message.files} onOpen={setOpened} />
          )}
          {message.role === "user" || !message.apps?.length ? null : (
            <TurnApps apps={message.apps} />
          )}
          {message.connect ? <ConnectLink connect={message.connect} /> : null}
          {meta.length ? (
            <Meta
              last={last}
              mine={mine}
              model={who === "agent" ? message.summary?.model : null}
              copy={
                who === "agent"
                  ? answer?.kind === "words"
                    ? answer.text
                    : undefined
                  : message.text
              }
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
      <MessageScrollerContent
        className={className}
        spacerClassName="order-last"
        aria-busy={live !== null}
      >
        {rows.length ? <Under>{children}</Under> : null}
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
                <TurnActivity working={row.working} runs={row.waiting} sources={row.sources} />
                <Said who="agent" entering>
                  <StreamingBody text={row.body} />
                </Said>
                {row.record.files.length ? (
                  <TurnFiles files={row.record.files} onOpen={setOpened} />
                ) : null}
                {row.record.apps.length ? <TurnApps apps={row.record.apps} /> : null}
                {row.record.connect ? <ConnectLink connect={row.record.connect} /> : null}
              </Speech>
            </MessageScrollerItem>
          ) : (
            bubble(row.message, row.key, index === rows.length - 1)
          ),
        )}
        {rows.length ? null : children}
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

/** The moment a message landed reaches the member under the pointer, never as a line of its own:
 *  a transcript reads as words rather than as a log. */
function Speech({
  mine,
  at,
  speaker,
  children,
}: {
  mine: boolean;
  at?: string;
  speaker?: Speaker;
  children: ReactNode;
}) {
  const content = <MessageContent title={at ? fullMoment(at) : undefined}>{children}</MessageContent>;
  if (!speaker) return <Message align={mine ? "end" : "start"}>{content}</Message>;
  return (
    <Message align="start">
      <Attributed speaker={speaker}>{content}</Attributed>
    </Message>
  );
}

/** The block reserves this as padding and the face is placed into it from the bubble, so the gutter
 *  and what hangs in it read off one expression. */
const FACE_GUTTER = "calc(var(--size-control) + var(--spacing-sm))";

/** The face answers to the bubble, not to this block: the moment line reserves a row under the
 *  bubble whether or not it shows, and a face centred on all of that sits visibly low. */
function Attributed({ speaker, children }: { speaker: Speaker; children: ReactNode }) {
  return (
    <div className="flex w-full min-w-0 flex-col gap-2xs" style={{ paddingInlineStart: FACE_GUTTER }}>
      <BubbleHeader speaker={speaker} />
      {children}
    </div>
  );
}

/** `Bubble` is the positioned box, so the face centres on the bubble's own height. */
function SpeakerFace({ speaker }: { speaker: Speaker }) {
  return (
    <FaceCircle
      name={speaker.name}
      photo={photoAddress(speaker.photo_url)}
      tint={speaker.email || speaker.name}
      title={speaker.name}
      className="absolute top-1/2 size-(--size-control) -translate-y-1/2"
      style={{ insetInlineStart: `calc(-1 * ${FACE_GUTTER})` }}
    />
  );
}

/** A channel several people speak in is read by side before a name is, which is why a colleague is
 *  not drawn as the viewer: left on the neutral fill against right on the accent. */
type Who = "me" | "member" | "agent";

function Said({
  who,
  speaker,
  entering = false,
  children,
}: {
  who: Who;
  speaker?: Speaker;
  entering?: boolean;
  children: ReactNode;
}) {
  const mine = who === "me";
  return (
    <Bubble
      variant={who === "agent" ? "ghost" : mine ? "said" : "default"}
      entering={entering}
      align={mine ? "end" : "start"}
      data-role={who}
    >
      {speaker ? <SpeakerFace speaker={speaker} /> : null}
      <BubbleContent>{children}</BubbleContent>
    </Bubble>
  );
}

export function OpenedFile({
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
