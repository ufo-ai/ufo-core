import { IconArticle, IconList } from "@tabler/icons-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Header } from "@/kernel/pane";
import { cn } from "@/lib/cn";
import type { Crumb } from "@/lib/title";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";

/** What stands in a slot, which is the whole of what decides the width it takes. */
export type SlotKind = "index" | "reading" | "panel";

type Entry = { id: string; kind: SlotKind; title: string | undefined };

/** The track after an act taken in `from` opened `id`: every lane after `from` shuts and `id`
 *  stands at its right. The track is the path the member walked, not a shelf of everything they
 *  have pressed — three documents opened one after another from the same list are three tries at
 *  one question, and the two they left behind are what makes the third hard to read.
 *
 *  `from` undefined is the pane's own list, the root, so an act taken there leaves exactly one lane
 *  standing. A `from` that no longer stands is the root too: a lane that has been shut raised
 *  nothing. A lane already standing further up the path moves rather than doubling — the track is
 *  keyed by id, and the same id twice is two hosts fighting over one record.
 *
 *  Re-opening the lane that already stands to `from`'s right hands back the very array it was
 *  given, not a copy of it. The caller places what comes back, and a fresh array of the same ids
 *  is a new placement: a history entry for a press that changed nothing, and a remount of the
 *  record the member is in the middle of reading.
 *
 *  A press from the lane at the far end grows the track, so it stops at `TRACK_MAX_SLOTS` like any
 *  other: the track comes back untouched, and the member reaches the record by pressing from a lane
 *  further up the path, which shortens the track before it stands. */
export function opened(track: string[], id: string, from?: string): string[] {
  const at = from === undefined ? -1 : track.indexOf(from);
  if (track[at + 1] === id) return track;
  const next = [...track.slice(0, at + 1).filter((held) => held !== id), id];
  return next.length > TRACK_MAX_SLOTS ? track : next;
}

/** The track after a deliberate open-beside: `id` stands at the far end and nothing shuts. This is
 *  how a member holds two unrelated things at once, having asked for it.
 *
 *  Nothing shuts here, so a full track has nowhere to put the lane and comes back untouched. The
 *  press cannot be answered by dropping the lane at the head: that lane is what the path was opened
 *  from, and an act whose whole meaning is that nothing shuts cannot shut the one lane the rest of
 *  the row was reached through. The row says it is full while it is, so the press that does nothing
 *  is a press the member can already see the answer to. */
export function appended(track: string[], id: string): string[] {
  if (track.includes(id) || track.length >= TRACK_MAX_SLOTS) return track;
  return [...track, id];
}

/** The track after `id` is shut, which shuts what was opened from it: a lane reached through
 *  another says nothing once the lane it was reached through is gone. */
export function closed(track: string[], id: string): string[] {
  const at = track.indexOf(id);
  if (at < 0) return track;
  return track.slice(0, at);
}

/** The track after the member carried `id` onto where `onto` stands: it lands in that place and
 *  the lanes from there on shift along. A lane the track does not name cannot be carried into it —
 *  a form an act raised stands beside the lanes without the address ever holding it — so the track
 *  comes back untouched and the caller writes nothing. */
function lifted(track: string[], id: string, onto: string): string[] {
  const at = track.indexOf(id);
  const to = track.indexOf(onto);
  if (at < 0 || to < 0 || at === to) return track;
  const next = [...track];
  next.splice(at, 1);
  next.splice(to, 0, id);
  return next;
}

const MIDDLE_BUTTON = 1;

/** Whether a press asked for its target to stand beside the track rather than replace the path
 *  under the lane it was raised in. It is the gesture the browser already handed the member for a
 *  second tab — command or control held, or the middle button — so there is nothing here to teach.
 *  A plain press and this one arrive as the same kind of event, so a call site that never reads it
 *  throws away the distinction the member drew with their hand. */
export function beside(event: { metaKey: boolean; ctrlKey: boolean; button?: number }): boolean {
  return event.metaKey || event.ctrlKey || event.button === MIDDLE_BUTTON;
}

type Track = {
  hosted: boolean;
  hosts: Map<string, HTMLElement>;
  place: (entry: Entry, after: string | undefined) => void;
  drop: (id: string) => void;
  move: ((id: string, onto: string) => void) | undefined;
};

const TrackContext = createContext<Track>({
  hosted: false,
  hosts: new Map(),
  place: () => {},
  drop: () => {},
  move: undefined,
});

const HereContext = createContext<string | undefined>(undefined);

const LIFTED = "text/plain";

const ROW = "flex min-h-0 gap-px overflow-x-auto bg-edge max-narrow:snap-x max-narrow:snap-mandatory";
const TRACK = "relative flex-1 " + ROW;
const OVER = "absolute inset-y-0 end-0 z-10 max-w-full " + ROW;

const PAGED =
  "max-narrow:min-w-full max-narrow:basis-full max-narrow:grow-0 max-narrow:shrink-0 " +
  "max-narrow:snap-start";

const WIDTHS: Record<SlotKind, string> = {
  index: "grow-0 shrink-0 basis-(--container-index)",
  reading: "grow shrink basis-0 min-w-(--size-slot-min)",
  panel: "grow-0 shrink basis-(--container-record) min-w-(--size-slot-min)",
};

/** A lane takes focus when it opens and when a member closes the one beside it, and draws nothing
 *  for it: the page-wide focus outline would stand around the whole lane, and a lane is not a
 *  control the member is about to press. */
const SLOT = "relative flex min-h-0 flex-col bg-surface outline-none " + PAGED;
const BODY = "flex min-h-0 flex-col bg-surface " + WIDTHS.reading + " " + PAGED;

const FULL =
  "m-0 flex min-h-0 items-center bg-surface px-2xl text-small text-ink-soft " +
  WIDTHS.index +
  " " +
  PAGED;

const FULL_NOTE = "This screen holds " + TRACK_MAX_SLOTS + " slots. Close one to open another.";

/** The mark a lane wears before its title. An index and a reading stand for a body of something, so
 *  a track holding several says which each one is; a panel stands for nothing but itself and its
 *  title is the whole of its head. Every kind states its answer here, so a kind added later cannot
 *  reach the band unmarked by saying nothing. */
const GLYPHS: Record<SlotKind, typeof IconList | null> = {
  index: IconList,
  reading: IconArticle,
  panel: null,
};

/** The lanes an address of `opens` stands, in the order it stands them. A lane it names stands
 *  where it names it however the lanes happened to mount — a link followed inside one lane at
 *  another already standing moves that lane without touching what the lane itself was handed, and a
 *  track waiting to be told again would draw the row the member walked away from. A lane the
 *  address does not name is an act's own form, opened beside the lane that raised it, and it keeps
 *  the place it was put in while the named lanes fill the places between. */
function inOrder(entries: Entry[], opens: string[]): Entry[] {
  const held = new Map(entries.map((entry) => [entry.id, entry]));
  const named: Entry[] = [];
  for (const id of opens) {
    const entry = held.get(id);
    if (entry && !named.includes(entry)) named.push(entry);
  }
  if (named.length < 2) return entries;
  const wanted = new Set(named);
  let next = 0;
  const standing = entries.map((entry) => (wanted.has(entry) ? named[next++] : entry));
  return standing.every((entry, at) => entry === entries[at]) ? entries : standing;
}

/** The path a pane holds, lane by lane in the order the member walked it. A lane is filled through
 *  a portal from wherever the view that wants it happens to sit: a create form belongs beside the
 *  list it will join, but the list is several components deep inside the page's own scroller — and
 *  a lane has to be a sibling of that scroller, not a child of it, or it scrolls away with the
 *  rows.
 *
 *  The wrapper is always the same element, `display: contents` while the track holds nothing,
 *  because a wrapper that appeared only when something opened would move every list one level down
 *  the tree the moment it did — and React reads that as a different component, unmounts the list,
 *  and takes with it the very state that asked for the lane. Each lane's host is `contents` for the
 *  same reason at one remove: the host stands a commit before the portal reaches it, and a host
 *  that were a box would flash an empty column that wide.
 *
 *  The pane's own body stands in the track as a reading lane in all but name, and takes that rule
 *  from the same place the lanes do. `display: contents` promotes whatever a view rendered to be
 *  the flex item, and a page div sized by its content would hold whatever width its widest table
 *  asked for and hand the lanes what was left.
 *
 *  The address owns the order the lanes stand in. It is what a link states, what the store holds,
 *  what a crumb reads as the lane to the left, and what shutting a lane cuts from, so a track that
 *  kept an order of its own would leave every one of those reading a row the member is not looking
 *  at. `opens` is that order and `onMove` is where a lane carried by hand is written, which is why
 *  they arrive together: a track that could be read without being written would take a drag nothing
 *  records. A track handed neither stands its lanes in the order they were opened and makes no band
 *  a handle, because a lane moved there would be moved somewhere nothing keeps.
 *
 *  Nothing is evicted by width. A member who walked six lanes deep still has six: they divide the
 *  width down to `--size-slot-min` and past that the track scrolls sideways, so the sixth costs the
 *  five before it nothing they can no longer be read at. What ends a lane is the member opening
 *  something else from further up the path, or shutting it. The hairline between two lanes is this
 *  element's own background showing through a one-pixel gap, so a lane carries no edge of its own
 *  and closing one leaves no seam where it stood.
 *
 *  What does end the row is `TRACK_MAX_SLOTS`, past which neither the address nor the store can
 *  carry it whole. A press there stands nothing, so the row states the cap and the one act that
 *  lifts it, standing where the next lane would have gone: the member reads why the press did
 *  nothing in the place the press was aimed at, and closing any lane takes the line away. It stands
 *  only where an address holds the track, since a row nothing writes has no cap to reach.
 *
 *  `over` is the host a lane keeps for the acts raised inside it. Such an act cannot be drawn in
 *  the lane it is already standing in: filling that lane means emptying it of the panel, which
 *  unmounts the very tab that raised the act and takes the act with it. So the lane's own track
 *  lies over it instead — the opener stays mounted underneath, and the form still opens beside
 *  rather than over the middle of the screen. The cover reaches only as far as what stands in it:
 *  anchored at the track's end, no wider than its lanes, never over the openers it was raised from.
 *  A cover across the whole host takes the next act with it — an app's connections and the act that
 *  adds one sit side by side under it, so opening one lane would lock out the opener of the next,
 *  which is a thing only a real browser can feel. */
export function SlotTrack({
  over = false,
  opens,
  onMove,
  children,
}: {
  over?: boolean;
  children: ReactNode;
} & (
  | { opens: string[]; onMove: (opens: string[]) => void }
  | { opens?: undefined; onMove?: undefined }
)) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [hosts, setHosts] = useState<Map<string, HTMLElement>>(new Map());
  const place = useCallback(
    (entry: Entry, after: string | undefined) =>
      setEntries((held) => {
        const at = held.findIndex((slot) => slot.id === entry.id);
        if (at < 0) {
          const parent = held.findIndex((slot) => slot.id === after);
          const next = [...held];
          next.splice(parent < 0 ? held.length : parent + 1, 0, entry);
          return next;
        }
        if (held[at].kind === entry.kind && held[at].title === entry.title) return held;
        const next = [...held];
        next[at] = entry;
        return next;
      }),
    [],
  );
  const drop = useCallback(
    (id: string) => setEntries((held) => held.filter((slot) => slot.id !== id)),
    [],
  );
  // A lane is a drag handle only where a drag can land somewhere: one lane alone has no order to
  // restate, so its band is a band and not a grip.
  const move = useMemo(
    () =>
      opens === undefined || onMove === undefined || opens.length < 2
        ? undefined
        : (id: string, onto: string) => {
            const next = lifted(opens, id, onto);
            if (next !== opens) onMove(next);
          },
    [opens, onMove],
  );
  const hold = useCallback(
    (id: string, host: HTMLElement | null) =>
      setHosts((held) => {
        const next = new Map(held);
        if (host) next.set(id, host);
        else next.delete(id);
        return next;
      }),
    [],
  );
  const track = useMemo(
    () => ({ hosted: true, hosts, place, drop, move }),
    [hosts, place, drop, move],
  );
  const standing = opens === undefined ? entries : inOrder(entries, opens);
  const shown = standing.length > 0;
  const lanes = standing.map((entry) => <SlotHost key={entry.id} id={entry.id} hold={hold} />);
  const full = shown && opens !== undefined && opens.length >= TRACK_MAX_SLOTS;
  return (
    <TrackContext.Provider value={track}>
      {over ? (
        <>
          {children}
          <div data-slot="slot-track" className={shown ? OVER : "contents"}>
            {lanes}
            {full ? <p className={FULL}>{FULL_NOTE}</p> : null}
          </div>
        </>
      ) : (
        <div data-slot="slot-track" className={shown ? TRACK : "contents"}>
          <div className={shown ? BODY : "contents"}>{children}</div>
          {lanes}
          {full ? <p className={FULL}>{FULL_NOTE}</p> : null}
        </div>
      )}
    </TrackContext.Provider>
  );
}

function SlotHost({
  id,
  hold,
}: {
  id: string;
  hold: (id: string, host: HTMLElement | null) => void;
}) {
  const keep = useCallback((host: HTMLElement | null) => hold(id, host), [id, hold]);
  return <div ref={keep} className="contents" />;
}

/** Puts a node in the track and states that a lane is wanted. Handed null it wants none, and the
 *  track gives the width back to the lanes that remain.
 *
 *  `id` is the caller's own name for the thing standing there — the record's id, the memory's name,
 *  a form's one word. The address carries the track as a list of these, and the store holds the
 *  same list per screen, so a name minted per render would give the same lane a different word on
 *  every load and neither a link nor a reorder could be written back.
 *
 *  A view drawn outside any track — a panel mounted on its own — has none to reach, and there the
 *  node stands where it was returned. Under a track it waits for its own host element instead of
 *  standing inline for a frame first: an element that moves into a portal is a different child in
 *  that position, so React would tear the node down and build it again, firing every read inside it
 *  twice.
 *
 *  What this does is register, never navigate: it says a lane is wanted and, where the track holds
 *  no address for it, puts it immediately to the right of the lane the caller stands in. Where the
 *  track has one, that address says where the lane stands. The rule that shortens a path is
 *  `opened`, and it belongs to the press that took the member somewhere — a screen rendering the
 *  lanes it already holds is not pressing anything, and a registration that truncated would shut
 *  every sibling lane as it mounted.
 *
 *  The lane's band is the portal's one header, so a lane, a page and a record state their name, what
 *  they hold and the way out of them in the same places. `crumb` is the surface the lane was reached
 *  from, said there as a step of the trail: a lane paged one to a screen has no lane standing to its
 *  left to read as the way back, so the band carries it. It states where the lane came from and
 *  never shuts it — the way out of a lane is the lane's own verb. The name is a heading under the
 *  page's own either way — the crumb is context, not the name, so a lane that states where it came
 *  from is no less a section of the screen than one that does not.
 *
 *  A lane that can be shut is one the member opened: it takes focus as it lands, answers Escape by
 *  shutting, and hands focus back to the act that raised it — but only if it still holds it, since
 *  a lane opened over another may unmount a commit after its replacement has already focused
 *  itself. It lands on itself only where what it holds has claimed nothing: a lane whose content
 *  opens on a field the member is meant to type in — a composer — is entered at that field, and
 *  Escape still reaches the lane from inside it. Escape is taken only where nothing else has
 *  claimed it: a select open inside the form answers that key first, and a lane that shut on it
 *  would take the whole form away when the member meant to close a menu.
 *
 *  Landing there is the whole of what a member who cannot see the lane is handed, so `describes`
 *  points at the line inside it that says what it does. A panel that states what it will do with
 *  everything typed into it says that to the member standing in it, or says it to nobody. */
export function useSlot(
  node: ReactNode | null,
  slot: {
    id: string;
    kind?: SlotKind;
    title?: string;
    /** Controls the lane's own header carries — a panel whose acts belong to what it holds. */
    acts?: ReactNode;
    /** The id of the line inside the lane that states what it does. */
    describes?: string;
    crumb?: Crumb;
    onClose?: () => void;
  },
): ReactNode {
  const { hosted, hosts, place, drop, move } = useContext(TrackContext);
  const here = useContext(HereContext);
  const { id, kind = "reading", title, acts, describes, crumb, onClose } = slot;
  const on = node !== null;
  const held = useRef<HTMLElement | null>(null);
  const host = hosts.get(id);

  useEffect(() => {
    if (!on) return;
    return () => drop(id);
  }, [on, id, drop]);

  useEffect(() => {
    if (!on) return;
    place({ id, kind, title }, here);
  }, [on, id, kind, title, here, place]);

  const takes = on && host !== undefined && onClose !== undefined;
  useEffect(() => {
    if (!takes) return;
    const before = document.activeElement;
    const panel = held.current;
    if (!panel?.contains(document.activeElement)) panel?.focus();
    return () => {
      if (!(before instanceof HTMLElement) || !document.body.contains(before)) return;
      const active = document.activeElement;
      const claimed =
        active instanceof HTMLElement && active !== document.body && !panel?.contains(active);
      if (claimed) return;
      before.focus();
    };
  }, [takes]);

  const land = useCallback((panel: HTMLElement | null) => {
    held.current = panel;
    panel?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []);

  if (!on) return null;
  if (!hosted) return node;
  if (!host) return null;
  const Glyph = GLYPHS[kind];
  return createPortal(
    <HereContext.Provider value={id}>
      <section
        ref={land}
        aria-label={title}
        aria-describedby={describes}
        tabIndex={-1}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || event.defaultPrevented || !onClose) return;
          event.stopPropagation();
          onClose();
        }}
        onDragOver={move && ((event) => event.preventDefault())}
        onDrop={
          move &&
          ((event) => {
            event.preventDefault();
            move(event.dataTransfer.getData(LIFTED), id);
          })
        }
        className={cn(SLOT, WIDTHS[kind])}
        style={{ "--pane-acts-inset": "0px" } as CSSProperties}
      >
        <Header
          pinned
          heading={2}
          {...(Glyph ? { glyph: <Glyph aria-hidden /> } : {})}
          crumb={crumb}
          title={title}
          acts={
            acts === undefined ? undefined : (
              <span
                className="contents"
                onDragStart={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                }}
              >
                {acts}
              </span>
            )
          }
          onClose={onClose}
          closes={title}
          onLift={move && ((event) => event.dataTransfer.setData(LIFTED, id))}
        />
        <div className="flex min-h-0 flex-1 flex-col">{node}</div>
      </section>
    </HereContext.Provider>,
    host,
  );
}
