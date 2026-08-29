import {
  IconArrowsDiagonal,
  IconArrowsDiagonalMinimize2,
  IconArticle,
  IconList,
} from "@tabler/icons-react";
import {
  animate,
  motionValue,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
  type MotionValue,
} from "motion/react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MutableRefObject,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { Banded, Header } from "@/kernel/pane";
import { cn } from "@/lib/cn";
import { GLYPH_STROKE } from "@/lib/glyph";
import { soundMoved, soundOpened } from "@/lib/sound";
import type { Crumb } from "@/lib/title";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";
import { useNarrow } from "@/lib/narrow";

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
  over: boolean;
  hosts: Map<string, HTMLElement>;
  place: (entry: Entry, after: string | undefined) => void;
  drop: (id: string) => void;
  move: ((id: string, onto: string) => void) | undefined;
  arrivals: MutableRefObject<Set<string>>;
  sought: MutableRefObject<Set<string>>;
  typing: string | undefined;
  watch: (id: string, panel: HTMLElement | null) => void;
  near: (id: string) => boolean;
  shows: (id: string) => void;
  expandable: boolean;
  expanded: string | undefined;
  expand: (id: string | undefined) => void;
};

const TrackContext = createContext<Track>({
  hosted: false,
  over: false,
  hosts: new Map(),
  place: () => {},
  drop: () => {},
  move: undefined,
  arrivals: { current: new Set() },
  sought: { current: new Set() },
  typing: undefined,
  watch: () => {},
  near: () => true,
  shows: () => {},
  expandable: false,
  expanded: undefined,
  expand: () => {},
});

/** A press that asks for one lane to be brought into view and says what an expanded row does. It is
 *  a value per press rather than the lane's id, because two presses on the same tile are two asks —
 *  the member scrolled away between them — and a track keyed on the id alone would answer the
 *  second with nothing. */
export type Seek = { id: string; expansion: "switch" | "restore" };

const NEAREST: ScrollIntoViewOptions = { block: "nearest", inline: "nearest" };

/** Focus that is not also a scroll. Every place the track takes focus has already scrolled the lane
 *  where it wants it, and a browser scrolling again to the element it just focused fights the row's
 *  snap and leaves it resting between two lanes. */
const KEEP: FocusOptions = { preventScroll: true };

/** How far past either edge of the scrollport a lane's body is still drawn: one scrollport, so the
 *  lane the member is scrolling toward stands filled by the time it reaches them. Percentages here
 *  resolve against the root's own box. */
const NEAR_MARGIN = "0px 100%";

/** Whether a lane's box stands within a scrollport of the row's, which is the same reach the
 *  observer is rooted with and is how a lane already in view draws its body in the frame it stands
 *  in rather than in the one after the observer's first batch. A row of no width has been laid out
 *  by nothing — jsdom's, and a row measured before its first layout — so no lane in it is far. */
function standsNear(port: DOMRect, lane: DOMRect): boolean {
  return (
    port.width === 0 || (lane.right > port.left - port.width && lane.left < port.right + port.width)
  );
}

const FAR = "min-h-0 flex-1 bg-surface";

/** The keys that walk the row, pressed bare: `]` to the next lane, `[` to the one before. Linear
 *  spends this pair on the same act, so a member arrives already holding it. The screen that lists
 *  the shortcuts reads them from here, so what it prints cannot drift from what the track answers. */
export const LANE_PRIOR = "[";
export const LANE_NEXT = "]";

/** What a track's row wears, which is how the row nearest the focused element is found. Every lane
 *  stands inside the row that draws it, so the nearest row above focus is the track the member is
 *  standing in. */
const ROW_MARK = "[data-slot=slot-track]";

/** What has already taken the keys where it stands open. A track that answered under one of these
 *  would take the member out of the thing they just opened. */
export const CLAIMED = "[role=listbox],[role=menu],[data-state=open]";

export const DIALOG = "dialog[open],[role=dialog]";

/** What is taking the keys as text. A bare bracket is a character before it is a shortcut, so a
 *  press that lands in a field the member is typing in is theirs and the row never sees it. */
export const TYPING = "input,textarea,select,[contenteditable]:not([contenteditable=false])";

const HereContext = createContext<string | undefined>(undefined);

const LIFTED = "text/plain";

const ROW = "flex min-h-0 gap-px bg-edge";
const TRACK = "relative flex-1 overflow-x-auto max-narrow:snap-x max-narrow:snap-mandatory " + ROW;

/** A row handed the whole pane never scrolls. Its lanes divide it exactly and it carries the rest of
 *  them on a running offset instead, each lane translated to the place that offset puts it, so what
 *  stands past either edge is clipped rather than reachable by a scrollbar. */
/** A phone pages the row one lane to a screen the way the base row does, scrolled and snapped by
 *  the finger: a phone has no bracket keys, no rail, and no wheel, so the ticker that turns the
 *  row at a desk would leave every lane past the first out of reach. */
const OVER =
  "absolute inset-0 z-10 overflow-hidden border-x border-edge " +
  "max-narrow:overflow-x-auto max-narrow:snap-x max-narrow:snap-mandatory " +
  ROW;

const PAGED =
  "max-narrow:min-w-full max-narrow:basis-full max-narrow:grow-0 max-narrow:shrink-0 " +
  "max-narrow:snap-start";

const WIDTHS: Record<SlotKind, string> = {
  index: "grow-0 shrink-0 basis-(--container-index)",
  reading: "grow shrink basis-0 min-w-(--size-slot-min)",
  panel: "grow-0 shrink basis-(--container-record) min-w-(--size-slot-min)",
};

/** The floor a lane holds, read off the row to count how many of them its width has room for. */
const SLOT_FLOOR = "--size-slot-min";

/** The gap the row draws between one lane and the next, which a lane's share of the width gives
 *  back so the lanes and the lines between them come to exactly the row. */
const HAIRLINE = 1;

/** What the row's width leaves room for: how many lanes divide it, and the width they divide. */
type Fit = { lanes: number; width: number };

/** What the row has room for at that floor. A row no layout has measured, and a floor no stylesheet
 *  answers for, are one answer: none of it is known, and every lane the row holds takes an equal
 *  share of it instead. */
function fitting(row: HTMLElement): Fit | null {
  const floor = Number.parseFloat(getComputedStyle(row).getPropertyValue(SLOT_FLOOR));
  const width = row.getBoundingClientRect().width;
  if (!Number.isFinite(floor) || floor <= 0 || width <= 0) return null;
  return { lanes: Math.max(1, Math.floor(width / floor)), width };
}

/** How far past either edge of the row a lane still stands drawn, in lanes: one, so the lane
 *  coming toward the member is filled by the time it reaches the edge. */
const REACH = 1;

/** What the row is carrying: whether it holds more lanes than fit, the distance from one lane's
 *  left edge to the next, how many stand on the screen at once, the lanes in the order the address
 *  states them, and whether this browser is asking for no movement. */
type Rolling = {
  wraps: boolean;
  step: number;
  lanes: number;
  ids: string[];
  still: boolean;
};

/** Where a lane whose place along the row is `spot` stands on the screen. The place is taken modulo
 *  the row's whole length, so the row has no end; past a lane's reach beyond the right edge the same
 *  place is read one length to the left instead, so the lanes the member has rotated by stand
 *  waiting on the near side rather than queued behind every other lane in the row. */
function placed(spot: number, offset: number, row: Rolling): number {
  if (!row.wraps) return spot - offset;
  const total = row.ids.length * row.step;
  const x = (((spot - offset) % total) + total) % total;
  return x > (row.lanes + REACH) * row.step ? x - total : x;
}

/** How the row settles on a lane boundary: a spring at a damping ratio of 0.87, which crosses the
 *  distance at once and stops on the boundary rather than passing it and coming back. */
const GLIDE = { type: "spring", stiffness: 300, damping: 30 } as const;

/** How long the wheel has to go quiet before the row settles. A trackpad flick arrives as a run of
 *  events, and a row that aligned itself between two of them would pull against a hand still
 *  moving. */
const SETTLE = 120;

const SLOT = "relative flex min-h-0 flex-col bg-surface";

/** What a lane wears where the track is handed the whole pane: the share of the row it takes, which
 *  the row itself states as how many lanes fit across it. Where the track stands beside a body the
 *  lane is sized by what it holds instead, and pages one to a screen at the narrow width. */
const SPREAD = "lane-share will-change-transform " + PAGED;

/** What a lane wears while it holds the member's cursor: the palette's focus stroke, drawn inside
 *  the lane's own edge so the row says which lane the words are going into. The stroke is on every
 *  lane, so it reads the same arriving as leaving, and it takes nothing from the lanes beside it —
 *  each of them stands at full strength and is read, scrolled and typed into as before. The clear
 *  stroke stands back where the lane itself is focus-visible, so a lane the keyboard walks to with
 *  `[` or `]` draws the palette's stroke rather than painting it away. */
const LIT =
  "outline-2 -outline-offset-2 outline-transparent focus-visible:outline-ring transition-colors duration-200 ease-control";
const LIT_ON = "outline-ring";

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
 *  Nothing is evicted by width. A member who walked six lanes deep still has six. A track handed the
 *  whole pane fills it: the lanes on screen divide the row's width exactly, as many of them as
 *  `--size-slot-min` leaves room for, and the rest wait off the edges at that same width. So three
 *  lanes take a third of the pane each rather than a lane's worth with the pane's width left over,
 *  and a ninth costs the eight before it nothing. A track standing beside a body sizes each lane by
 *  what it holds instead, down to `--size-slot-min`, and past that the track scrolls sideways. What
 *  ends a lane is the member opening something else from further up the path, or shutting it. The
 *  hairline between two lanes is this element's own background showing through a one-pixel gap, so a
 *  lane carries no edge of its own and closing one leaves no seam where it stood — and it is the
 *  pixel a share subtracts, so the lanes and the hairlines between them come to exactly the row.
 *
 *  A row wider than the pane it fills has no end: it carries one running offset in pixels, and each
 *  lane is translated to that offset taken modulo the whole row's length, so rotating past the last
 *  lane brings the first one back rather than stopping against a wall. The lanes hold their places
 *  in the document — the address, the focus order and the drag handles all read the row the member
 *  arranged — and only the transform moves, which is what a browser animates without laying anything
 *  out again. Where every lane fits, nothing wraps: the offset rests at zero and the row is what it
 *  looks like. The hand moves the row by the horizontal wheel, which the row takes over from the
 *  browser and settles on the nearest lane boundary once it goes quiet; the pointer's own drag is
 *  the band's, which carries a lane to a new place in the address, so the row is never dragged by
 *  it. A track standing beside a body scrolls sideways in the ordinary way instead, snapping to lane
 *  boundaries at the narrow width where a lane is the whole screen.
 *
 *  What a lane holds is drawn only near the row. Every lane keeps its width and its band, so the
 *  row's width, the drag handles and the address hold still, but a body standing more than a lane
 *  past either edge is an empty surface until it comes round: a row of twenty app pages is twenty
 *  frames loading at once, for a member who can see three. The endless row reads that off the same
 *  offset the transforms come from, so a lane is filled by the place it stands in rather than by
 *  anything the browser has to observe. A track beside a body has a scrollport to watch instead, and
 *  one observer per track watches its lanes against it, rooted on the row with a scrollport of
 *  margin: the track measures the row and each lane itself as they stand, before the frame is
 *  painted, because an observer's first batch arrives a task after the paint and a track that waited
 *  for it would draw every lane on the screen empty on the way in. What a body held comes back with
 *  it — chat state lives in stores keyed by lane, and an app page loads again in its frame — which
 *  is the whole cost of the rule. A track nothing can observe, jsdom's, draws every body, and so
 *  does a row no layout has given a width.
 *
 *  The lane holding the member's cursor draws the focus stroke. A row is several open things at once
 *  by design, so the lane the words are going into says so on its own edge; nothing is taken from the
 *  lanes beside it, which stand at full strength and are read, scrolled and typed into as before. A
 *  press into a second lane's field moves the stroke there. What ends it is what ended the typing —
 *  Escape out of the field, or focus landing anywhere that is not one, on this row or off it. It is
 *  the cursor that is read, not the press: a lane focused at a button draws no stroke.
 *
 *  `seek` brings one lane into view: the endless row rotates until that lane stands at its head, and
 *  a row beside a body scrolls to it — a lane not yet standing waits and is brought in as it lands.
 *  A seek is a movement and nothing else, so the lane it names takes no focus — the member asked to
 *  see it, not to stand in it, and a rail tile that emptied the field they were typing in would cost
 *  them the words. A lane arriving by a press does take focus, because the press was the ask to
 *  stand in it.
 *
 *  `[` and `]` walk the row: `]` toward the next lane, `[` toward the one before, and both wrap, so
 *  a row wider than the screen is reachable without a pointer or a tab through everything each lane
 *  holds. On the endless row the press moves the row itself, one lane per press, and focus stays
 *  where the member left it: the row is what came out from under them, so a press that also carried
 *  the cursor away would cost them the lane they were reading. Where every lane already fits there
 *  is nothing to bring round and the press does nothing. A row beside a body has no offset to move,
 *  so there the press stands the member in the next lane and scrolls it into view. They are pressed
 *  bare, which is what Linear spends them on over its own lists: walking the row is the act a member
 *  repeats most, and a chord costs a hand for it every time. What a bare key costs instead is that
 *  it is also a character, so the row yields — a press inside an input, a textarea, a select or
 *  anything editable is what the member is typing and the track never sees it, and neither does a
 *  press a menu, a select or a dialog has claimed, or one another handler has already taken. A track
 *  answers only where the row it draws is the nearest track row above the focused element, and where
 *  focus stands outside every track, only a row no other row encloses answers — two nested tracks
 *  never both move on one press.
 *
 *  What does end the row is `TRACK_MAX_SLOTS`, past which neither the address nor the store can
 *  carry it whole. A press there stands nothing, so the row states the cap and the one act that
 *  lifts it, standing where the next lane would have gone: the member reads why the press did
 *  nothing in the place the press was aimed at, and closing any lane takes the line away. It stands
 *  only where an address holds the track, since a row nothing writes has no cap to reach, and only
 *  where the row has an end to stand it at — a row that comes round has no place that is not a lane.
 *
 *  `over` is the host a lane keeps for the acts raised inside it. Such an act cannot be drawn in
 *  the lane it is already standing in: filling that lane means emptying it of the panel, which
 *  unmounts the very tab that raised the act and takes the act with it. So the lane's own track
 *  lies over it instead — the opener stays mounted underneath, and the track covers the whole of
 *  the pane, edge to edge, its lanes splitting that width in equal shares. A screen whose every
 *  lane is a lane — home — is the cover's whole use: the pane under it holds nothing a member
 *  could press, so covering all of it locks nothing out. */
export function SlotTrack({
  over = false,
  opens,
  onMove,
  seek,
  children,
}: {
  over?: boolean;
  seek?: Seek;
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
  const [expanded, setExpanded] = useState<string | undefined>(undefined);
  const expand = useCallback((id: string | undefined) => setExpanded(id), []);
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
  /* The lanes that arrived by a press, told apart from a row the screen stood whole: a lane joining
     a row already standing, or one lane joining an empty row, is an arrival — it scrolls into view
     and may take focus as it lands. A screen mounting several lanes at once is restating a row the
     member already arranged, and a track that scrolled or focused each of those in turn would open
     paged to the last lane rather than the first. */
  const arrivals = useRef<Set<string>>(new Set());
  const sought = useRef<Set<string>>(new Set());
  const prior = useRef<Set<string> | null>(null);
  const panels = useRef<Map<string, HTMLElement>>(new Map());
  const named = useRef<Map<Element, string>>(new Map());
  const order = useRef<string[]>([]);
  const watcher = useRef<IntersectionObserver | null>(null);
  const [row, setRow] = useState<HTMLElement | null>(null);
  const narrow = useNarrow();
  const [fit, setFit] = useState<Fit | null>(null);
  const [nearby, setNearby] = useState<ReadonlySet<string>>(() => new Set());
  const culls = typeof IntersectionObserver !== "undefined";
  /* The observer serves every row that scrolls — a track beside a body, and the whole-pane track
     wherever it does not wrap: every lane fitting, or a phone paging it by hand. Only an endless
     row reads its lanes off the offset instead, since nothing scrolls there. */
  useLayoutEffect(() => {
    if (!culls || !row || (over && rolling.current.wraps)) return;
    const watching = new IntersectionObserver(
      (seen) =>
        setNearby((held) => {
          const next = new Set(held);
          for (const entry of seen) {
            const id = named.current.get(entry.target);
            if (id === undefined) continue;
            if (entry.isIntersecting) next.add(id);
            else next.delete(id);
          }
          return next.size === held.size && [...next].every((id) => held.has(id)) ? held : next;
        }),
      { root: row, rootMargin: NEAR_MARGIN },
    );
    const port = row.getBoundingClientRect();
    const seen = new Set<string>();
    for (const [id, panel] of panels.current) {
      watching.observe(panel);
      if (standsNear(port, panel.getBoundingClientRect())) seen.add(id);
    }
    watcher.current = watching;
    setNearby(seen);
    return () => {
      watching.disconnect();
      watcher.current = null;
    };
  }, [culls, row, over, narrow, fit, entries.length]);
  const watch = useCallback(
    (id: string, panel: HTMLElement | null) => {
      if (panel) {
        panels.current.set(id, panel);
        named.current.set(panel, id);
        watcher.current?.observe(panel);
        const port = watcher.current && row ? row.getBoundingClientRect() : undefined;
        if (port && standsNear(port, panel.getBoundingClientRect()))
          setNearby((held) => (held.has(id) ? held : new Set(held).add(id)));
        return;
      }
      const stood = panels.current.get(id);
      if (!stood) return;
      watcher.current?.unobserve(stood);
      panels.current.delete(id);
      named.current.delete(stood);
      setNearby((held) => {
        if (!held.has(id)) return held;
        const next = new Set(held);
        next.delete(id);
        return next;
      });
    },
    [row],
  );
  const [typing, setTyping] = useState<string | undefined>(undefined);
  const writing = useCallback((held: EventTarget | null) => {
    if (!(held instanceof HTMLElement)) return undefined;
    if (!held.closest(TYPING) && !held.isContentEditable) return undefined;
    for (const [id, panel] of panels.current) if (panel.contains(held)) return id;
    return undefined;
  }, []);
  /* The lanes reach the row through portals, so a React focus handler on the row would never hear
     them — synthetic events climb the component tree, and a lane's parent there is the view that
     asked for it. The row listens on the DOM instead, where the lanes are its descendants. */
  useEffect(() => {
    if (!row) return;
    const entered = (event: FocusEvent) => setTyping(writing(event.target));
    const left = (event: FocusEvent) => setTyping(writing(event.relatedTarget));
    row.addEventListener("focusin", entered);
    row.addEventListener("focusout", left);
    return () => {
      row.removeEventListener("focusin", entered);
      row.removeEventListener("focusout", left);
    };
  }, [row, writing]);
  useLayoutEffect(() => {
    if (!over || !row || typeof ResizeObserver === "undefined") return;
    const sizes = new ResizeObserver(() => setFit(fitting(row)));
    sizes.observe(row);
    setFit(fitting(row));
    return () => sizes.disconnect();
  }, [over, row]);
  const offset = useMotionValue(0);
  const still = useReducedMotion();
  /* What the row is carrying, read by every handler that moves it: they run on events rather than
     on a render, and a row that answered a press with the width it had two lanes ago would step by
     a lane that is no longer a lane wide. */
  const rolling = useRef<Rolling>({ wraps: false, step: 0, lanes: 0, ids: [], still: false });
  /* Where the row is headed, which is what the next press steps from. `offset` is mid-flight while
     an earlier press is still settling, so a press that stepped from there would arrive short of a
     lane and leave the row resting between two. */
  const aimed = useRef(0);
  const spots = useRef<Map<string, MotionValue<number>>>(new Map());
  const seated = useRef<Map<string, number>>(new Map());
  const [ticking, setTicking] = useState<ReadonlySet<string>>(() => new Set());
  const paint = useCallback(() => {
    if (!over) return;
    const { step, lanes, ids } = rolling.current;
    const drawn = new Set<string>();
    for (const [at, id] of ids.entries()) {
      const panel = panels.current.get(id);
      if (step === 0) {
        if (panel) panel.style.transform = "";
        drawn.add(id);
        continue;
      }
      const spot = spots.current.get(id);
      const x = placed(spot === undefined ? at * step : spot.get(), offset.get(), rolling.current);
      if (panel) panel.style.transform = "translateX(" + Math.round((x - at * step) * 100) / 100 + "px)";
      if (x >= -REACH * step && x <= (lanes + REACH) * step) drawn.add(id);
    }
    if (offset.isAnimating() || [...spots.current.values()].some((spot) => spot.isAnimating()))
      return;
    setTicking((was) =>
      was.size === drawn.size && [...drawn].every((id) => was.has(id)) ? was : drawn,
    );
  }, [offset, over]);
  /* A body mounts only once the row is still. Mounting a transcript or a frame is the heaviest
     work this screen does, and doing it in the middle of a glide is what drops its frames — so
     while the offset or any lane is in flight the drawn set is left as it was, and the landing
     paints it once. Flight is read off the motion values themselves rather than counted, so a
     glide cut short by the next press leaves nothing stuck in the air. */
  const settle = useCallback(
    (flight: unknown) => {
      void Promise.resolve(flight).then(paint);
    },
    [paint],
  );
  useMotionValueEvent(offset, "change", paint);
  const paced = useRef(0);
  useLayoutEffect(() => {
    if (!over) return;
    const { wraps, step, ids, still: fixed } = rolling.current;
    const was = paced.current;
    const resized = was !== step;
    paced.current = step;
    if (!wraps) {
      aimed.current = 0;
      if (offset.get() !== 0) offset.jump(0);
    } else {
      if (resized && was > 0) aimed.current = Math.round(aimed.current / was) * step;
      const total = ids.length * step;
      if (Math.abs(aimed.current) >= total)
        aimed.current = ((aimed.current % total) + total) % total;
      if (aimed.current !== offset.get()) offset.jump(aimed.current);
    }
    const held = new Set(ids);
    for (const [id, spot] of spots.current) {
      if (held.has(id)) continue;
      spot.destroy();
      spots.current.delete(id);
      seated.current.delete(id);
    }
    for (const [at, id] of ids.entries()) {
      const to = at * step;
      const spot = spots.current.get(id);
      if (spot === undefined) {
        const made = motionValue(to);
        made.on("change", paint);
        spots.current.set(id, made);
        seated.current.set(id, to);
        continue;
      }
      if (resized) {
        spot.jump(to);
        seated.current.set(id, to);
        continue;
      }
      if (seated.current.get(id) === to) continue;
      seated.current.set(id, to);
      const total = ids.length * step;
      const way = wraps ? to + Math.round((spot.get() - to) / total) * total : to;
      if (fixed) spot.jump(way);
      else settle(animate(spot, way, GLIDE));
    }
    paint();
  });
  useEffect(
    () => () => {
      for (const spot of spots.current.values()) spot.destroy();
      spots.current.clear();
      seated.current.clear();
    },
    [],
  );
  const glide = useCallback(
    (to: number) => {
      aimed.current = to;
      if (rolling.current.still) offset.jump(to);
      else settle(animate(offset, to, GLIDE));
    },
    [offset, settle],
  );
  const shows = useCallback(
    (id: string) => {
      const { wraps, step, ids } = rolling.current;
      if (!wraps) {
        panels.current.get(id)?.scrollIntoView(NEAREST);
        return;
      }
      const at = ids.indexOf(id);
      if (at >= 0) glide(at * step);
    },
    [glide],
  );
  const standing = opens === undefined ? entries : inOrder(entries, opens);
  const ids = standing.map((entry) => entry.id);
  /* The one lane the row is standing expanded: the raw ask read against the row it asks about, as
     an expansion the row no longer holds — its lane taken over by a pick, or a row down to a single
     lane — is no expansion at all. It stands before everything that answers about a lane, because
     every one of those answers must be this same answer: a `near` reading the raw ask while the
     lanes read this one empties every body in the commit that ends an expansion. */
  const focused =
    expanded !== undefined && ids.length > 1 && ids.includes(expanded) ? expanded : undefined;
  const near = useCallback(
    (id: string) =>
      focused !== undefined
        ? id === focused
        : over && rolling.current.wraps
          ? ticking.has(id)
          : !culls || nearby.has(id),
    [focused, over, ticking, culls, nearby, narrow, fit],
  );
  useEffect(() => {
    if (!seek) return;
    setExpanded((held) =>
      seek.expansion === "restore" || held === undefined ? undefined : seek.id,
    );
    if (!panels.current.has(seek.id)) {
      sought.current.add(seek.id);
      return;
    }
    shows(seek.id);
    soundMoved();
  }, [seek, shows]);
  useEffect(() => {
    if (!row) return;
    const rotate = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.metaKey || event.ctrlKey) return;
      const way = event.key === LANE_NEXT ? 1 : event.key === LANE_PRIOR ? -1 : 0;
      const ids = order.current;
      if (way === 0 || ids.length < 2) return;
      const active = document.activeElement;
      if (active?.closest(TYPING) || (active instanceof HTMLElement && active.isContentEditable))
        return;
      if (active?.closest(CLAIMED) || document.querySelector(DIALOG)) return;
      const nearest = active?.closest(ROW_MARK) ?? null;
      const answers = nearest === null ? !row.parentElement?.closest(ROW_MARK) : nearest === row;
      if (!answers) return;
      const { wraps, step } = rolling.current;
      if (over) {
        if (!wraps) return;
        event.preventDefault();
        glide(aimed.current + way * step);
        soundMoved();
        return;
      }
      const stood = ids.findIndex((id) => panels.current.get(id)?.contains(active));
      const port = row.getBoundingClientRect();
      const seen = ids.filter((id) => {
        const lane = panels.current.get(id)?.getBoundingClientRect();
        return lane !== undefined && lane.right > port.left && lane.left < port.right;
      });
      const edge = seen.length === 0 ? 0 : ids.indexOf(way > 0 ? seen[0] : seen[seen.length - 1]);
      const at = ((stood < 0 ? edge : stood) + way + ids.length) % ids.length;
      const panel = panels.current.get(ids[at]);
      if (!panel) return;
      event.preventDefault();
      panel.focus(KEEP);
      panel.scrollIntoView(NEAREST);
      soundMoved();
    };
    document.addEventListener("keydown", rotate);
    return () => document.removeEventListener("keydown", rotate);
  }, [row, over, glide]);
  useEffect(() => {
    if (!row || !over) return;
    let quiet: ReturnType<typeof setTimeout> | undefined;
    const rolls = (event: WheelEvent) => {
      if (!rolling.current.wraps || Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
      event.preventDefault();
      aimed.current = offset.get() + event.deltaX;
      offset.jump(aimed.current);
      clearTimeout(quiet);
      quiet = setTimeout(() => {
        const { step } = rolling.current;
        glide(Math.round(aimed.current / step) * step);
      }, SETTLE);
    };
    row.addEventListener("wheel", rolls, { passive: false });
    return () => {
      row.removeEventListener("wheel", rolls);
      clearTimeout(quiet);
    };
  }, [row, over, offset, glide]);
  useEffect(() => {
    if (expanded !== undefined && focused === undefined) setExpanded(undefined);
  }, [expanded, focused]);
  const track = useMemo(
    () => ({
      hosted: true,
      over,
      hosts,
      place,
      drop,
      move: focused === undefined ? move : undefined,
      arrivals,
      sought,
      typing,
      watch,
      near,
      shows,
      expandable: over && ids.length > 1,
      expanded: focused,
      expand,
    }),
    [over, hosts, place, drop, move, typing, watch, near, shows, ids.length, focused, expand],
  );
  if (prior.current !== null && (prior.current.size > 0 || ids.length === 1)) {
    for (const id of ids) if (!prior.current.has(id)) arrivals.current.add(id);
  }
  useEffect(() => {
    prior.current = new Set(ids);
    order.current = ids;
  });
  const wraps = focused === undefined && over && !narrow && fit !== null && ids.length > fit.lanes;
  const share =
    focused === undefined
      ? Math.max(1, fit === null ? ids.length : Math.min(ids.length, fit.lanes))
      : 1;
  rolling.current = {
    wraps,
    step: fit === null ? 0 : (fit.width - (share - 1) * HAIRLINE) / share + HAIRLINE,
    lanes: fit?.lanes ?? 0,
    ids: focused === undefined ? ids : [focused],
    still: still === true,
  };
  const shown = standing.length > 0;
  const lanes = standing.map((entry) => <SlotHost key={entry.id} id={entry.id} hold={hold} />);
  const full = shown && !wraps && opens !== undefined && opens.length >= TRACK_MAX_SLOTS;
  return (
    <TrackContext.Provider value={track}>
      {over ? (
        <>
          {children}
          <div
            ref={setRow}
            data-slot="slot-track"
            className={shown ? OVER : "contents"}
            style={{ "--lane-share": String(share) } as CSSProperties}
          >
            {lanes}
            {full ? <p className={FULL}>{FULL_NOTE}</p> : null}
          </div>
        </>
      ) : (
        <div
          ref={setRow}
          data-slot="slot-track"
          className={shown ? TRACK : "contents"}
        >
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
 *  they hold and the way out of them in the same places. It is drawn whole wherever the lane stands:
 *  a page under a band of its own is still a page, and the lane it opens beside itself is a surface
 *  that band never named. `crumb` is the surface the lane was reached
 *  from, said there as a step of the trail: a lane paged one to a screen has no lane standing to its
 *  left to read as the way back, so the band carries it. It states where the lane came from and
 *  never shuts it — the way out of a lane is the lane's own verb. The name is a heading under the
 *  page's own either way — the crumb is context, not the name, so a lane that states where it came
 *  from is no less a section of the screen than one that does not.
 *
 *  A lane that can be shut is one the member opened: it takes focus as it lands and hands focus back
 *  to the act that raised it — but only if it still holds it, since a lane opened over another may
 *  unmount a commit after its replacement has already focused itself. It lands on itself only where
 *  what it holds has claimed nothing: a lane whose content opens on a field the member is meant to
 *  type in — a composer — is entered at that field.
 *
 *  Escape in a lane does one thing: pressed in a field the lane holds it leaves the field, landing
 *  focus on the lane itself, where the bracket keys walk the row again. It shuts nothing. A row is
 *  several open things at once, and a key that took one of them away would spend a member's whole
 *  row on a mistyped press; the way out of a lane is the close control on its band, which names the
 *  lane it shuts. Pressed anywhere else in the lane it is nobody's — a select open inside a form
 *  answers it first, and the lane never sees it.
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
    /** The mark the band wears before the title, where the kind's own says less than the lane
     *  holds — a lane standing an app wears that app's mark. Null is a band with no mark. */
    glyph?: ReactNode;
    /** The surface the lane wears where the track's own reads wrong — a lane that is an offer
     *  rather than a page takes the filled tone, band and body in one coat. */
    tone?: string;
    /** A lane the member cannot carry: its band is never a handle, though the rest of the row still
     *  reorders around it. */
    fixed?: boolean;
    /** Controls the lane's own header carries — a panel whose acts belong to what it holds. */
    acts?: ReactNode;
    /** The id of the line inside the lane that states what it does. */
    describes?: string;
    crumb?: Crumb;
    onClose?: () => void;
  },
): ReactNode {
  const {
    hosted,
    over,
    hosts,
    place,
    drop,
    move,
    arrivals,
    sought,
    typing,
    watch,
    near,
    shows,
    expandable,
    expanded,
    expand,
  } = useContext(TrackContext);
  const here = useContext(HereContext);
  const {
    id,
    kind = "reading",
    title,
    glyph,
    tone,
    fixed = false,
    acts,
    describes,
    crumb,
    onClose,
  } = slot;
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

  const arrived = useRef(false);
  const takes = on && host !== undefined && onClose !== undefined;
  useEffect(() => {
    if (!takes) return;
    const before = document.activeElement;
    const panel = held.current;
    if (arrived.current && !panel?.contains(document.activeElement)) panel?.focus(KEEP);
    return () => {
      if (!(before instanceof HTMLElement) || !document.body.contains(before)) return;
      const active = document.activeElement;
      const claimed =
        active instanceof HTMLElement && active !== document.body && !panel?.contains(active);
      if (claimed) return;
      before.focus();
    };
  }, [takes]);

  const land = useCallback(
    (panel: HTMLElement | null) => {
      held.current = panel;
      watch(id, panel);
      if (!panel) return;
      const arriving = arrivals.current.delete(id);
      const asked = sought.current.delete(id);
      if (!arriving && !asked) return;
      if (arriving) {
        arrived.current = true;
        soundOpened();
      } else soundMoved();
      shows(id);
    },
    [arrivals, sought, watch, shows, id],
  );

  if (!on) return null;
  if (!hosted) return node;
  if (!host) return null;
  const Glyph = GLYPHS[kind];
  const lit = typing === id;
  const hidden = expanded !== undefined && expanded !== id;
  const expandedHere = expanded === id;
  const expandAct = expandable ? (
    <Button
      variant="mark"
      size="glyph"
      aria-label={expandedHere ? "Show all lanes" : "Expand " + (title ?? "lane")}
      onClick={() => expand(expandedHere ? undefined : id)}
    >
      {expandedHere ? (
        <IconArrowsDiagonalMinimize2 aria-hidden stroke={GLYPH_STROKE} />
      ) : (
        <IconArrowsDiagonal aria-hidden stroke={GLYPH_STROKE} />
      )}
    </Button>
  ) : null;
  return createPortal(
    <Banded value={false}>
      <HereContext.Provider value={id}>
        <section
          ref={land}
          aria-label={title}
          aria-describedby={describes}
          hidden={hidden}
          tabIndex={-1}
          onKeyDown={(event) => {
            if (event.key !== "Escape" || event.defaultPrevented) return;
            const from = event.target;
            if (!(from instanceof HTMLElement)) return;
            if (!from.closest(TYPING) && !from.isContentEditable) return;
            event.stopPropagation();
            event.preventDefault();
            event.currentTarget.focus(KEEP);
          }}
          onDragOver={move && ((event) => event.preventDefault())}
          onDrop={
            move &&
            ((event) => {
              event.preventDefault();
              move(event.dataTransfer.getData(LIFTED), id);
            })
          }
          data-lit={lit ? "" : undefined}
          className={cn(
            SLOT,
            over ? SPREAD : [WIDTHS[kind], PAGED],
            LIT,
            lit && LIT_ON,
            hidden && "hidden",
            tone,
          )}
          style={{ "--pane-acts-inset": "0px" } as CSSProperties}
        >
          <Header
            pinned
            ruled
            heading={2}
            {...(glyph !== undefined ? { glyph } : Glyph ? { glyph: <Glyph aria-hidden /> } : {})}
            crumb={crumb}
            title={title}
            acts={
              acts === undefined && expandAct === null ? undefined : (
                <span
                  className="contents"
                  onDragStart={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                  }}
                >
                  {acts}
                  {expandAct}
                </span>
              )
            }
            onClose={expandedHere ? undefined : onClose}
            closes={title}
            onLift={
              move && !fixed ? (event) => event.dataTransfer.setData(LIFTED, id) : undefined
            }
            onDoubleClick={
              expandable
                ? (event) => {
                    const target = event.target;
                    if (target instanceof Element && target.closest("button,a")) return;
                    expand(expandedHere ? undefined : id);
                  }
                : undefined
            }
          />
          {near(id) ? (
            <div className="flex min-h-0 flex-1 flex-col">{node}</div>
          ) : (
            <div className={FAR} />
          )}
        </section>
      </HereContext.Provider>
    </Banded>,
    host,
  );
}
