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
import { agentHash } from "@/lib/route";
import { formatSize } from "@/lib/size";
import { eventLabel, latestActivity } from "@/lib/turnStream";
import type { ActivityEvent, Bubble as Spoken, LiveTurn } from "@/lib/chatStore";
import type { ChatApp, ChatConnect, ChatFile, ChatQuestion, SubagentRun } from "@/lib/types";

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

/** The element whose scrollbar moves this node: the nearest ancestor that scrolls its overflow —
 *  the live chat's own pane, a linked conversation's column — or the page itself when nothing
 *  nearer does. */
function scrollerOf(node: Element): Element {
  for (let held = node.parentElement; held !== null; held = held.parentElement) {
    const overflow = getComputedStyle(held).overflowY;
    if (overflow === "auto" || overflow === "scroll") return held;
  }
  return document.scrollingElement ?? document.documentElement;
}

/** Where a node stands in its scroller's content, independent of how far that content is
 *  scrolled — the page scroller's own rect already carries its scroll, an inner pane's does
 *  not. */
function placeOf(node: Element, scroller: Element): number {
  const top = node.getBoundingClientRect().top - scroller.getBoundingClientRect().top;
  return scroller === document.scrollingElement ? top : top + scroller.scrollTop;
}

/** A transcript read back opens at its end, the way the live chat's pane lands there: the newest
 *  thing is the thing being said. The pane decides that for itself; a transcript standing in a
 *  column or page that scrolls has no pane, so the opening scroll is stated here, once, on
 *  mount. */
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

/** The row above the oldest loaded message of a compacted conversation. Scrolled into view, it
 *  loads the page above and holds the line being read exactly where it was — the next page's
 *  height is added above the reading line, so the same height is put back on the scroller. The
 *  measure anchors on the row's next sibling rather than the scroller's height: a reply streaming
 *  in below would otherwise ride into the correction. A load that failed stops watching and waits
 *  to be pressed, so a dead route is asked once rather than on every scroll. Once every page is
 *  in, the row stays as an empty item rather than unmounting: the last page's correction runs in
 *  the very commit that draws the page, and only a mounted row can run it. */
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
 *  other file as a card. Pressing any file opens it in the shared sheet.
 *
 *  `children` is what the screen hangs at the foot of the transcript, in the same column — a
 *  handoff, an empty state — so nothing floats over the conversation in a pane of its own. */
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
  /** The compacted-away pages above `messages`, for a conversation that has them: the loaded ones
   *  draw above the tail, and the row over the oldest brings in the next as the reader scrolls
   *  up. A pane without one draws the tail as the whole conversation. */
  earlier?: EarlierMessages;
  live?: LiveTurn | null;
  question?: (asked: ChatQuestion) => ReactNode;
  /** The reading column, set on the messages rather than on the pane that scrolls them: a pane
   *  narrowed to the column carries the scrollbar at the column's edge, which puts a moving bar
   *  in the middle of the screen beside the words instead of at the side of the window. */
  className?: string;
  children?: ReactNode;
  /** The file a link from another surface named, opened once it is on screen and once only. */
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
            <Activity events={message.events ?? []} runs={message.subagents ?? []} live={false} />
          )}
          {/* A message of files alone says everything it says in the files: a bubble over them
              would be an empty surface where words never were. */}
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
          {message.meta ? <Meta>{message.meta}</Meta> : null}
          {message.question && question ? question(message.question) : null}
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
                {row.live.meter ? <Meta>{row.live.meter}</Meta> : null}
                {row.live.meta ? <Meta>{row.live.meta}</Meta> : null}
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

/** The file a member pressed, in the set it was pressed out of: a turn's pictures open as one set
 *  so the viewer can step between them, and every other file opens as a set of one. */
type Opened = { files: ChatFile[]; at: number };

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

/** A file's name is the whole of the control that opens it, and a name set in a small line is 15px
 *  tall. At a phone width the control keeps the control height as the box a finger has to land on,
 *  which the name itself does not decide. */
const TAP_FLOOR = "max-narrow:inline-flex max-narrow:min-h-(--size-control) max-narrow:items-center";

/** What a reply's turn shared, under the words that shared it. Images with a preview come first:
 *  one is drawn as part of the answer, and several stand in share order on one snapping row. Every
 *  other shared file follows in a card grid, including documents whose first page has a preview and
 *  images no preview was rendered for — only a drawable image belongs in the image carousel.
 *  Pressing a preview goes to `onOpen`, as does every file card. The write-up the reply carried
 *  comes first, as the link that opens it: it belongs to the answer, the attachments to the turn. */
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

/** The write-up a reply carried, as the link that opens it in the sheet a file card opens: a link in
 *  dress and a button in kind. A file with no url has nothing to open and is named in plain text. */
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

/** The applications a reply's turn created, under the words that made them. One row per app — its
 *  mark, its name, and the model it runs on — and the row is the link that opens it, so a member
 *  reaches what they just asked for without going looking for it. Two apps read as two rows of one
 *  card, never a card of cards: the reply is already a document in the reading column. */
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

/** What the member attached to their own words, above them and ending on the same edge as the
 *  bubble: the message reads as the things and then the words, which is the order they were sent in.
 *  A file this page is still sending is drawn off the file in hand — the conversation's workspace,
 *  which every later read draws it from, does not hold it yet — and one a read stated is drawn off
 *  the link that read carried. */
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

/** One shared picture, named by its filename — a file that is itself a picture, or the first page a
 *  document was rendered to, which wears a badge naming the kind of document it came from. */
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

/** What pressing a shared file opens. A picture opens in the viewer that fills the window, because
 *  reading a picture is a matter of size and the panel beside the transcript has none to give; every
 *  other file opens in the shelf's own sheet, where its characters, its first page, or its download
 *  is the whole of what there is to read. */
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

export function Meta({ children }: { children: ReactNode }) {
  return (
    <Marker className="mt-2xs font-mono text-small tabular-nums">
      <MarkerContent>{children}</MarkerContent>
    </Marker>
  );
}

/** The nine dots of the throbber, in the order they are drawn: where each one sits in the icon's own
 *  24-unit box, which dot of the braille cell it carries, and whether it stands in the column that
 *  runs a tick ahead of the rest. */
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

/** The glyph at the head of a line the turn is still on: the nine round dots the line has always
 *  carried, pulsing as they always have, and lit to spell one braille character per tick through
 *  ⠋⠙⠹⠸⠼⠴⠦⠧.
 *
 *  A braille cell is six dots, two columns of three; the grid is nine, three columns of three. So a
 *  character stands in the left two columns of the grid, written the way braille is written — dots
 *  1, 2, 3 down the left column, dots 4, 5, 6 down the one beside it — and the right-hand column
 *  holds the first column of the character coming next. A character therefore reaches the right edge
 *  of the grid a tick before it is read at the left of it, and the run travels across the dots.
 *
 *  The dots are drawn here rather than taken from the icon set, because each one has to say which
 *  dot of the cell it carries for the theme to light it, and because a dot has to be round: a cell
 *  drawn as a braille character is drawn by whatever font answers for braille, which squares the
 *  dots off. The circles keep the grid's own geometry — the same nine places in the same 24-unit
 *  box, the same stroke — so the line is the icon it always was.
 *
 *  Nothing is swapped in and nothing is redrawn: the dots stand where they are and only their ink
 *  changes, so the words beside them hold still. `theme.css` carries the six keyframes, one per dot
 *  of the cell, and `tests/throb.test.tsx` reads them back into the eight characters. It carries no
 *  words of its own: the line says what it is doing beside it, so a member hears the step rather
 *  than the spelling. */
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
      // The dots are centred on the words rather than on the line they sit in. A line box carries
      // the room a descender needs, so its middle falls a pixel below the middle of the letters
      // being read, and the grid centred in that box hangs the same pixel low against them. The
      // pixel is taken back here, which puts the middle of the nine dots on the middle of the
      // letters beside them — measured against the built sheet, the dots centre on the band the
      // lowercase runs through rather than on the descender's room.
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

/** The reply's activity disclosure, and while the turn runs the one line saying what the agent is
 *  doing. Live it leads with what is happening — `Awaiting N subagents` while runs are open, else
 *  the current step; settled it states `Completed N steps`: the reply's own steps, a run counting as
 *  one however much it did inside. It stands closed until the member opens it, so a turn that ran
 *  twenty steps costs the transcript the one line saying where it is rather than a column growing
 *  under the reply being read. A line the turn is still on carries a glyph at its head and takes the
 *  reading ink; a settled one takes the resting ink and the chevron that opens it.
 *
 *  A turn opens on this line with nothing yet behind it, and the first tool call arrives into the
 *  same line rather than replacing it. That is why the disclosure is drawn whether or not it has
 *  steps to disclose: swapping the element the moment the first step lands would take the words
 *  down with it and cut the glyphing short. With nothing behind it the line carries no chevron and
 *  does not open.
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
  const [open, setOpen] = useState(false);
  const steps = events.length + runs.length;
  if (!steps && working === undefined) return null;
  const openRuns = runs.filter((run) => run.running).length;
  const summary = live
    ? openRuns > 0
      ? "Awaiting " + openRuns + " subagent" + (openRuns === 1 ? "" : "s")
      : (working ?? latestActivity(events, runs))
    : "Completed " + steps + " step" + (steps === 1 ? "" : "s");
  const shown = steps > 0 && open;
  return (
    <details
      className="group/activity mt-2xs text-label text-ink-soft"
      open={shown}
      // A browser fires `toggle` for the `open` this render writes as well as for a member's own
      // click, so only a state the fold was not already drawn in came from the member.
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

/** What a delegated run's profile reads as when the run is another agent rather than a subagent
 *  profile: the rest of the profile is that agent's name. */
const AGENT_PROFILE = "agent:";

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

/** The private connect act a reply leaves for the member, and what that act became.
 *
 *  While the request stands it is the act: a chip carrying the provider's mark and name, opening the
 *  consent window on the press. Once the connect has landed the same chip states the account it
 *  made, pressing nothing — the words above it asked the member to connect, so a chip that vanished
 *  would leave that instruction pointing at nothing, and one still offering to connect would ask for
 *  an act already done. It is the shape a connected connector already takes on the first run. */
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
  // The address the press opens is this surface's own: it mints the consent URL for this turn's
  // request and redirects the window there, so the chip holds nothing that can go stale.
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
