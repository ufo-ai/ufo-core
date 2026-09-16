import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { DecodeLine, decodeFrame, settle, type SettledCell } from "@/components/ui/decode";
import { brailleOf, readBraille } from "@/lib/braille";

const TEXT = "reading the calendar";
const CELLS = Array.from(TEXT).length;
const DECODE_MS = 1_500;
const HOLD_MS = 1_200;
const FRAME_MS = 90;

/** The line with no accent on it, so a phase can be read without the ember in the way. */
const PLAIN: SettledCell[] = Array.from(TEXT, (character) => ({
  glyph: character === " " ? " " : brailleOf(character)!,
  accent: false,
}));

function frameAt(t: number, settled: SettledCell[] = PLAIN) {
  return decodeFrame(TEXT, t, 0, settled);
}

function glyphs(t: number, settled: SettledCell[] = PLAIN) {
  return frameAt(t, settled)
    .map((cell) => cell.glyph)
    .join("");
}

function stillMotion(reduce: boolean) {
  vi.stubGlobal("matchMedia", (media: string) => ({
    media,
    matches: reduce,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  vi.useRealTimers();
});

test("a letter takes the grade-1 cell that spells it, and the cipher reads back", () => {
  expect(Array.from("reading", brailleOf).join("")).toBe("⠗⠑⠁⠙⠊⠝⠛");
  expect(readBraille("⠗⠑⠁⠙⠊⠝⠛")).toBe("reading");
  expect(readBraille(glyphs(0))).toBe(TEXT);
});

test("the static phase holds the transliteration, and the churn leaves it", () => {
  expect(glyphs(0)).toBe(glyphs(0.34));
  expect(readBraille(glyphs(0.34))).toBe(TEXT);
  expect(readBraille(glyphs(0.5))).not.toBe(TEXT);
});

test("the ember drops out of the churn", () => {
  const embers: SettledCell[] = Array.from(TEXT, (character) =>
    character === " " ? { glyph: " ", accent: false } : { glyph: "∵", accent: true },
  );
  expect(frameAt(0.2, embers).some((cell) => cell.accent)).toBe(true);
  expect(frameAt(0.5, embers).some((cell) => cell.accent)).toBe(false);
  expect(frameAt(0.8, embers).some((cell) => cell.accent)).toBe(true);
});

test("the resolve front travels left to right and reaches the last cell", () => {
  expect(frameAt(0.69).filter((cell) => cell.resolved && cell.glyph !== " ")).toHaveLength(0);
  const front = frameAt(0.85);
  const resolved = front.map((cell, at) => (cell.resolved && cell.glyph !== " " ? at : -1));
  const ciphered = front.findIndex((cell) => !cell.resolved);
  expect(Math.max(...resolved)).toBeLessThan(ciphered);
  expect(glyphs(0.85).startsWith("reading")).toBe(true);
  expect(glyphs(1)).toBe(TEXT);
});

test("a resolved cell never reverts across the whole decode", () => {
  let behind = 0;
  for (let t = 0; t <= 1; t += 0.01) {
    const resolved = frameAt(t).filter((cell) => cell.resolved && cell.glyph !== " ").length;
    expect(resolved).toBeGreaterThanOrEqual(behind);
    behind = resolved;
  }
});

test("the cipher keeps the plaintext's length and its spaces at every frame", () => {
  for (let t = 0; t <= 1; t += 0.02) {
    const cells = frameAt(t);
    expect(cells).toHaveLength(CELLS);
    Array.from(TEXT).forEach((character, at) => {
      if (character === " ") expect(cells[at].glyph).toBe(" ");
      else expect(cells[at].glyph).not.toBe(" ");
    });
  }
});

test("accents fall on at most a seventh of the cells, and never on a space", () => {
  const written = Array.from(TEXT).filter((character) => character !== " ").length;
  for (let run = 0; run < 50; run += 1) {
    const settled = settle(TEXT);
    expect(settled.filter((cell) => cell.accent).length).toBeLessThanOrEqual(
      Math.round(written * 0.14),
    );
    Array.from(TEXT).forEach((character, at) => {
      if (character === " ") expect(settled[at]).toEqual({ glyph: " ", accent: false });
    });
  }
});

test("a character with no braille of its own settles on one cell and holds it", () => {
  const numbered = "opening pull request 1759";
  const settled = settle(numbered);
  const digits = Array.from(numbered, (_, at) => at).filter((at) => /[0-9]/.test(numbered[at]));
  expect(digits.length).toBe(4);
  for (const t of [0, 0.1, 0.34, 0.71]) {
    const cells = decodeFrame(numbered, t, 0, settled);
    for (const at of digits) expect(cells[at].glyph).toBe(settled[at].glyph);
  }
});

/** The tick is fixed, so the decode lands on the first frame at or past its nominal end rather than
 *  on the millisecond itself. The whole cycle divides evenly and the loop does return to frame 0. */
const RESOLVED_FRAME = Math.ceil(DECODE_MS / FRAME_MS);
const CYCLE_FRAMES = (DECODE_MS + HOLD_MS) / FRAME_MS;

const line = () => document.querySelector("[data-slot=decode-text]")?.textContent;
const step = (frames: number) => act(() => vi.advanceTimersByTimeAsync(frames * FRAME_MS));

/** A live line carries accents over some of its cells, so only the braille among them spells the
 *  message. Every cell that is braille must be the cell for the letter standing in that column. */
function spellsMessage(cipher: string): boolean {
  return Array.from(cipher).every((glyph, at) => {
    const code = glyph.codePointAt(0)!;
    return code < 0x2800 || code > 0x28ff || readBraille(glyph) === TEXT[at];
  });
}

test("a looping line opens on ciphertext, resolves, and holds before it runs again", async () => {
  vi.useFakeTimers();
  render(<DecodeLine text={TEXT} loop />);

  expect(line()).not.toBe(TEXT);
  expect(spellsMessage(line()!)).toBe(true);
  await step(RESOLVED_FRAME);
  expect(line()).toBe(TEXT);

  await step(CYCLE_FRAMES - RESOLVED_FRAME - 1);
  expect(line()).toBe(TEXT);

  await step(1);
  expect(line()).not.toBe(TEXT);
  expect(spellsMessage(line()!)).toBe(true);
});

test("the line steps on the frame, not between two of them", async () => {
  vi.useFakeTimers();
  render(<DecodeLine text={TEXT} />);
  expect(document.querySelectorAll("[data-slot=decode-cell]")).toHaveLength(
    Array.from(TEXT).filter((character) => character !== " ").length,
  );

  await step(RESOLVED_FRAME - 1);
  expect(line()).not.toBe(TEXT);
  await act(() => vi.advanceTimersByTimeAsync(FRAME_MS - 1));
  expect(line()).not.toBe(TEXT);
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(line()).toBe(TEXT);
});

test("a line that decodes once stops its timer once it has", async () => {
  vi.useFakeTimers();
  render(<DecodeLine text={TEXT} loop={false} />);
  await step(RESOLVED_FRAME);
  expect(line()).toBe(TEXT);
  expect(vi.getTimerCount()).toBe(0);

  await step(CYCLE_FRAMES);
  expect(line()).toBe(TEXT);
});

test("reduced motion renders the words and starts no timer", () => {
  stillMotion(true);
  vi.useFakeTimers();
  render(<DecodeLine text={TEXT} />);
  expect(screen.getByText(TEXT)).toBeTruthy();
  expect(document.querySelector("[data-slot=decode-text]")).toBeNull();
  expect(vi.getTimerCount()).toBe(0);
  stillMotion(false);
});

test("the churn stops when the tab is hidden and when the line goes away", async () => {
  vi.useFakeTimers();
  const view = render(<DecodeLine text={TEXT} />);
  await step(1);
  expect(vi.getTimerCount()).toBeGreaterThan(0);

  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
  expect(vi.getTimerCount()).toBe(0);

  hidden.mockReturnValue(false);
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
  expect(vi.getTimerCount()).toBeGreaterThan(0);

  view.unmount();
  expect(vi.getTimerCount()).toBe(0);
});

test("a spent line stays spent when the tab comes back", async () => {
  vi.useFakeTimers();
  render(<DecodeLine text={TEXT} loop={false} />);
  await step(RESOLVED_FRAME);
  expect(vi.getTimerCount()).toBe(0);

  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
  hidden.mockReturnValue(false);
  await act(async () => void document.dispatchEvent(new Event("visibilitychange")));

  expect(vi.getTimerCount()).toBe(0);
  await step(CYCLE_FRAMES);
  expect(line()).toBe(TEXT);
});

test("a new wait glyphs in from the churn rather than cutting to its own ciphertext", async () => {
  vi.useFakeTimers();
  const view = render(<DecodeLine text={TEXT} />);
  await step(RESOLVED_FRAME);
  expect(line()).toBe(TEXT);

  // The churn is the one phase whose glyphs are not the message, so a frame that reads back as the
  // new words would be the static cipher — the cut this replaced.
  const next = "checking the last invoice";
  view.rerender(<DecodeLine text={next} />);
  expect(line()).not.toBe(next);
  expect(line()).toHaveLength(next.length);
  expect(readBraille(line()!)).not.toBe(next);
  await step(1);
  expect(readBraille(line()!)).not.toBe(next);

  await step(RESOLVED_FRAME);
  expect(line()).toBe(next);
});

test("a line staggered behind another holds its opening frame until its turn", async () => {
  vi.useFakeTimers();
  render(<DecodeLine text={TEXT} delay={260} />);
  const opening = line();

  await act(() => vi.advanceTimersByTimeAsync(259));
  expect(line()).toBe(opening);
  await act(() => vi.advanceTimersByTimeAsync(1 + FRAME_MS * RESOLVED_FRAME));
  expect(line()).toBe(TEXT);
});

test("a wait that changes after the line has settled decodes again", async () => {
  vi.useFakeTimers();
  const view = render(<DecodeLine text={TEXT} />);
  await step(RESOLVED_FRAME);
  expect(line()).toBe(TEXT);

  // The line holds, spent, for as long as the step runs. The clock it decoded on is stopped.
  await step(RESOLVED_FRAME * 3);
  expect(line()).toBe(TEXT);

  const next = "checking the last invoice";
  view.rerender(<DecodeLine text={next} />);
  expect(line()).not.toBe(next);

  await step(RESOLVED_FRAME);
  expect(line()).toBe(next);
});
