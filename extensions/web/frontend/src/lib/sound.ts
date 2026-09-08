import { useSyncExternalStore } from "react";
import { tiks } from "@rexa-developer/tiks";

const HELD = "ufo.sound-muted";
const MUTED = "muted";
const UNMUTED = "unmuted";
const THEME = "soft";
const VOLUME = 0.3;

let started = false;
const listeners = new Set<() => void>();

export function soundMoved(): void {
  play(() => tiks.swoosh());
}

export function soundEnded(): void {
  play(() => tiks.click());
}

/** The engine holds an `AudioContext`, which a browser mints only on a member's own gesture and jsdom
 *  does not mint at all, so the first sound starts the engine and a browser without Web Audio stays silent. */
function play(sound: () => void): void {
  if (held()) return;
  if (!started) {
    if (typeof AudioContext === "undefined" && !("webkitAudioContext" in globalThis)) return;
    tiks.init({ theme: THEME, volume: VOLUME });
    started = true;
  }
  sound();
}

/** Unmuted where the browser hands back no store at all: a storage policy that blocks the site costs
 *  the member their pick, never the screen. */
function held(): boolean {
  try {
    return globalThis.localStorage?.getItem(HELD) === MUTED;
  } catch {
    return false;
  }
}

export function useMuted(): boolean {
  return useSyncExternalStore((listener) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }, held);
}

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
