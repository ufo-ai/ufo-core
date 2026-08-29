import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { setMuted, soundMoved, soundOpened, useMuted } from "@/lib/sound";

const speaker = vi.hoisted(() => ({
  init: vi.fn(),
  pop: vi.fn(),
  swoosh: vi.fn(),
  mute: vi.fn(),
  unmute: vi.fn(),
}));

vi.mock("@rexa-developer/tiks", () => ({ tiks: speaker }));

const HELD = "ufo.sound-muted";

beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal("AudioContext", class {});
});

/** The engine is started by the first sound and by nothing else, so a member who never opens a lane
 *  never holds an `AudioContext` — and the theme and the volume are settled in that one call. */
test("the first sound starts the speaker, and the sounds after it find it started", () => {
  soundOpened();
  soundMoved();
  soundOpened();

  expect(speaker.init).toHaveBeenCalledTimes(1);
  expect(speaker.init).toHaveBeenCalledWith({ theme: "soft", volume: 0.3 });
  expect(speaker.pop).toHaveBeenCalledTimes(2);
  expect(speaker.swoosh).toHaveBeenCalledTimes(1);
});

test("a muted browser plays nothing, and the engine is told as well", () => {
  setMuted(true);

  soundOpened();
  soundMoved();

  expect(speaker.pop).not.toHaveBeenCalled();
  expect(speaker.swoosh).not.toHaveBeenCalled();
  expect(speaker.mute).toHaveBeenCalledTimes(1);
});

test("the pick is held in this browser, and every reader of it follows", () => {
  const { result } = renderHook(() => useMuted());
  expect(result.current).toBe(false);

  act(() => setMuted(true));

  expect(result.current).toBe(true);
  expect(localStorage.getItem(HELD)).toBe("muted");

  act(() => setMuted(false));

  expect(result.current).toBe(false);
  expect(localStorage.getItem(HELD)).toBe("unmuted");
  expect(speaker.unmute).toHaveBeenCalledTimes(1);
});

test("a browser muted before this page loaded is muted when it loads", () => {
  localStorage.setItem(HELD, "muted");

  const { result } = renderHook(() => useMuted());
  soundOpened();

  expect(result.current).toBe(true);
  expect(speaker.pop).not.toHaveBeenCalled();
});
