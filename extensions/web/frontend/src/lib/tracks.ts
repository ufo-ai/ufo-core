import type { Section, WorkspaceTab } from "@/lib/route";

/** The screen a track stands on. Three id spaces reach this store — an app's own id, a workspace
 *  tab, a section — and a name means nothing across them: `connectors` is a section and a link to
 *  the workspace tab of that name reaches the same word, so a bare name would hand one screen
 *  another's slots. The space a name comes from leads it, which makes one key shape cover all
 *  three. */
export type TrackScreen = `agent:${string}` | `workspace:${WorkspaceTab}` | `section:${Section}`;

const TRACK_PREFIX = "ufo.track.";
const TRACK_SEPARATOR = "\n";
/** The line every value this module writes opens with, and one lane stands on every line under it.
 *  A value opening on anything else is a value this module did not write, and reads as no track at
 *  all rather than as a row of lanes nothing here can name. */
const TRACK_MARK = "ufo.lanes";
const TRACK_MAX_ID_CHARS = 256;

/** How many lanes a track stands, which is the one cap the address, the store and the verbs that
 *  open a lane all answer to. A press that would stand one more does nothing instead of shortening
 *  the track from the head: the head is the list or the record the whole path was opened from, and
 *  a track that shed it would state a path beginning nowhere — in the store a screen is read back
 *  from, and in the address a member hands to someone else. */
export const TRACK_MAX_SLOTS = 24;

/** What the key holds, or nothing where this browser hands back no store at all. */
function read(key: string): string | null {
  try {
    return globalThis.sessionStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** Hold `line` at the key, or drop the key where there is no track to hold. A browser that refuses
 *  the write — a quota reached, a storage policy that blocks the site — costs the member the way
 *  back to a row of lanes, which the address states anyway; a portal that would not mount over it
 *  costs them the screen. */
function hold(key: string, line: string | null): void {
  try {
    const held = globalThis.sessionStorage;
    if (line === null) held?.removeItem(key);
    else held?.setItem(key, line);
  } catch {
    return;
  }
}

/** Whether a slot id is one this store can hold and read back. The address refuses a track it
 *  cannot: a screen that opened its slots and then lost them the moment the member navigated is
 *  worse than a link that says outright it is not valid. */
export function holdable(id: string): boolean {
  return id.length > 0 && id.length <= TRACK_MAX_ID_CHARS && !id.includes(TRACK_SEPARATOR);
}

/** What keeps `slots` from being a track, or nothing where it is one: every id has to be one this
 *  store can hold, no id may stand twice — the same id in two lanes is two hosts over one record —
 *  and the row may be no longer than `TRACK_MAX_SLOTS`. Composed once here because the address and
 *  the store both refuse on it, so a row one of them turns away is a row the other turns away, for
 *  the same reason and in the same words. */
export function unholdable(slots: string[]): string | null {
  const id = slots.find((held) => !holdable(held));
  if (id !== undefined) return "A slot id no track can hold: " + JSON.stringify(id);
  if (new Set(slots).size !== slots.length)
    return "A track cannot hold one slot id twice: " + JSON.stringify(slots);
  if (slots.length > TRACK_MAX_SLOTS)
    return "A track stands at most " + TRACK_MAX_SLOTS + " slots: " + slots.length;
  return null;
}

/** Whether a row of ids is a track. This is what the address admits, so a link states a track a
 *  screen can stand on and come back to. */
export function holdableTrack(slots: string[]): boolean {
  return unholdable(slots) === null;
}

/** The lanes a screen was left holding, in the order they stand. A lane names what stands in it and
 *  the namespace it is read in alike, so a row of lanes is the whole of what a screen holds.
 *
 *  A track is a working arrangement, not a preference, so it lives as long as the tab the member is
 *  working in: a detour through another app comes back to the same row of slots, and the next
 *  morning opens on the screen's own default rather than on last night's six records. The address
 *  is what carries a track further — a link states its whole track — and it is the only copy a
 *  member can hand to someone else or come back to on purpose.
 *
 *  A key holding a value this module did not write — a mark that is not this one, a lane this store
 *  cannot round trip, no lane at all, since a screen holding none drops its key — reads as an empty
 *  track and is dropped: a screen the member can work from is worth more than an arrangement
 *  nothing here can name. */
export function heldTrack(screen: TrackScreen): string[] {
  const key = TRACK_PREFIX + screen;
  const raw = read(key);
  if (raw === null) return [];
  const [mark, ...slots] = raw.split(TRACK_SEPARATOR);
  if (mark !== TRACK_MARK || !slots.length || !holdableTrack(slots)) {
    hold(key, null);
    return [];
  }
  return slots;
}

/** Hold the lanes a screen stands on, so leaving it and coming back finds the same row in the same
 *  order. A row this store cannot round trip — a slot id it cannot hold, one id standing twice, or
 *  more lanes than a track stands — raises here, at the call that made it up, rather than reading
 *  back shorter than it was written. The verbs that open a lane stop at the cap, so the only way to
 *  reach this is a caller that made up a row no screen could have been left holding. */
export function holdTrack(screen: TrackScreen, slots: string[]): void {
  const fault = unholdable(slots);
  if (fault) throw new Error(fault);
  hold(TRACK_PREFIX + screen, slots.length ? [TRACK_MARK, ...slots].join(TRACK_SEPARATOR) : null);
}
