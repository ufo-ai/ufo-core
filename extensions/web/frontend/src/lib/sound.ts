import { useSyncExternalStore } from "react";
import { tiks } from "@rexa-developer/tiks";

/** The portal's one speaker. Two acts on the track are said out loud — a lane opening, and focus
 *  crossing to the lane beside it — because both move the screen under a member whose eyes are on
 *  the pane rather than on the pointer. Every sound is synthesised in the browser at the moment it
 *  plays: nothing is fetched, decoded, or held. */
const HELD = "ufo.sound-muted";
const MUTED = "muted";
const UNMUTED = "unmuted";
const THEME = "soft";
const VOLUME = 0.3;

let started = false;
const listeners = new Set<() => void>();

/** A lane opened. */
export function soundOpened(): void {
  play(() => tiks.pop());
}

/** Focus moved to another lane. */
export function soundMoved(): void {
  play(() => tiks.swoosh());
}

/** The engine holds an `AudioContext`, which a browser mints only on a member's own gesture and
 *  jsdom does not mint at all, so the first sound is what starts the engine, and a browser with no
 *  Web Audio stays silent rather than raising under the press that asked for the sound. */
function play(sound: () => void): void {
  if (held()) return;
  if (!started) {
    if (typeof AudioContext === "undefined" && !("webkitAudioContext" in globalThis)) return;
    tiks.init({ theme: THEME, volume: VOLUME });
    started = true;
  }
  sound();
}

/** Whether the browser holds the sounds back, or unmuted where it hands back no store at all: a
 *  storage policy that blocks the site costs the member their pick, never the screen. */
function held(): boolean {
  try {
    return globalThis.localStorage?.getItem(HELD) === MUTED;
  } catch {
    return false;
  }
}

/** Whether this browser is holding the sounds back. The rail's mark and every play read the one
 *  answer, so the mark states what the next lane will do rather than a copy of it kept elsewhere. */
export function useMuted(): boolean {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, held);
}

/** Take the pick and hold it in this browser. The engine is told as well, so a sound already
 *  scheduled falls silent with the press instead of playing out after it. */
export function setMuted(muted: boolean): void {
  try {
    globalThis.localStorage?.setItem(HELD, muted ? MUTED : UNMUTED);
  } catch {
    return;
  }
  if (muted) tiks.mute();
  else tiks.unmute();
  for (const listener of listeners) listener();
}
