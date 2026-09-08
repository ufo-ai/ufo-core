import type { Section, WorkspaceTab } from "@/lib/route";

export type TrackScreen =
  | "home"
  | `agent:${string}`
  | `workspace:${WorkspaceTab}`
  | `section:${Section}`;

const TRACK_PREFIX = "ufo.track.";
const TRACK_SEPARATOR = "\n";
const TRACK_MARK = "ufo.lanes";
const TRACK_MAX_ID_CHARS = 256;

/** The one cap the address, the store and the verbs that open a lane all answer to. A press that would
 *  stand one more does nothing rather than shortening the track from the head. */
export const TRACK_MAX_SLOTS = 24;

function read(key: string): string | null {
  try {
    return globalThis.sessionStorage?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

/** A browser that refuses the write — a quota reached, a storage policy that blocks the site — costs
 *  the member the way back to a row of lanes, which the address states anyway. */
function hold(key: string, line: string | null): void {
  try {
    const held = globalThis.sessionStorage;
    if (line === null) held?.removeItem(key);
    else held?.setItem(key, line);
  } catch {
    return;
  }
}

export function holdable(id: string): boolean {
  return id.length > 0 && id.length <= TRACK_MAX_ID_CHARS && !id.includes(TRACK_SEPARATOR);
}

/** One id in a track twice is two hosts over one record, and a row longer than the store holds is lanes
 *  the member would lose on the way back. */
export function unholdable(slots: string[]): string | null {
  const id = slots.find((held) => !holdable(held));
  if (id !== undefined) return "A slot id no track can hold: " + JSON.stringify(id);
  if (new Set(slots).size !== slots.length)
    return "A track cannot hold one slot id twice: " + JSON.stringify(slots);
  if (slots.length > TRACK_MAX_SLOTS)
    return "A track stands at most " + TRACK_MAX_SLOTS + " slots: " + slots.length;
  return null;
}

export function holdableTrack(slots: string[]): boolean {
  return unholdable(slots) === null;
}

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

/** A row this store cannot round trip raises here, at the call that made it up, rather than reading
 *  back shorter than it was written. */
export function holdTrack(screen: TrackScreen, slots: string[]): void {
  const fault = unholdable(slots);
  if (fault) throw new Error(fault);
  hold(TRACK_PREFIX + screen, slots.length ? [TRACK_MARK, ...slots].join(TRACK_SEPARATOR) : null);
}
