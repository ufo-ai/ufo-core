import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { brailleOf, randomCell } from "@/lib/braille";
import { cn } from "@/lib/cn";

/** The mark surfaces in the static oftener than the rest of the pool, so the shape a member already
 *  reads as ufo keeps coming back out of the noise. */
const ACCENTS = Array.from("∴∵∷⁘⁙⋮⋰⋱∧∨⊻⊼△▽◁▷⊳⊲⊙⊚⊛⊕⌾");
const ACCENT_MARK = "∵";
const ACCENT_MARK_WEIGHT = 2.5;
const ACCENT_SHARE = 0.14;

/** The decode splits by fraction of its own length: ciphertext, then a churn the accents drop out
 *  of, then a resolve front travelling left to right. */
const DECODE_MS = 1_500;
const HOLD_MS = 1_200;
const FRAME_MS = 90;
const STATIC_END = 0.35;
const CHURN_END = 0.7;

/** A wait that changes snaps to the new ciphertext and reads as a cut. Entering at the churn takes
 *  the old words apart and builds the new ones out of the same noise. */
const CHURN_FRAME = Math.ceil((STATIC_END * DECODE_MS) / FRAME_MS);

/** The accents breathe on their own period, offset per cell so the line shimmers rather than blinks
 *  in unison; every other unresolved cell takes a slow wave travelling along the line. */
const PULSE_PERIOD_S = 2.6;
const PULSE_CELL_PHASE = 0.11;
const RIPPLE_CELLS_PER_WAVE = 7;

export type DecodeCell = {
  glyph: string;
  resolved: boolean;
  accent: boolean;
  /** How far along its channel this cell sits, 0 to 100, for `color-mix`. */
  mix: number;
};

/** The glyph a cell shows whenever it is not churning, and whether it is one of the ember few. */
export type SettledCell = { glyph: string; accent: boolean };

function weightedAccent(): string {
  const total = ACCENTS.length - 1 + ACCENT_MARK_WEIGHT;
  let ticket = Math.random() * total;
  for (const accent of ACCENTS) {
    ticket -= accent === ACCENT_MARK ? ACCENT_MARK_WEIGHT : 1;
    if (ticket < 0) return accent;
  }
  return ACCENT_MARK;
}

/** The line every phase but the churn draws, settled once: which cells carry an accent and which
 *  accent each carries, and the one cell a character with no braille of its own — a digit, a
 *  bracket — stands behind. Rolling either per frame would leave the static phase twitching and
 *  would multiply the ember. A space is never an accent, and never anything but itself. */
export function settle(text: string): SettledCell[] {
  const places = Array.from(text, (character, index) => (character === " " ? -1 : index)).filter(
    (index) => index !== -1,
  );
  const wanted = Math.round(places.length * ACCENT_SHARE);
  const chosen = new Set<number>();
  while (chosen.size < wanted && chosen.size < places.length) {
    chosen.add(places[Math.floor(Math.random() * places.length)]);
  }
  return Array.from(text, (character, index) => {
    if (character === " ") return { glyph: character, accent: false };
    if (chosen.has(index)) return { glyph: weightedAccent(), accent: true };
    return { glyph: brailleOf(character) ?? randomCell(), accent: false };
  });
}

function pulseMix(index: number, now: number): number {
  return ((Math.sin((now * 2 * Math.PI) / PULSE_PERIOD_S + index * PULSE_CELL_PHASE) + 1) / 2) * 100;
}

function rippleMix(index: number, now: number): number {
  return (
    ((Math.sin((index / RIPPLE_CELLS_PER_WAVE - now) * 2 * Math.PI) + 1) / 2) * 100
  );
}

/** The line as it stands at progress `t` through the decode, on a clock of `now` seconds. Pure: the
 *  same arguments draw the same line, so the phases can be asserted without a timer. */
export function decodeFrame(
  text: string,
  t: number,
  now: number,
  settled: SettledCell[],
): DecodeCell[] {
  const characters = Array.from(text);
  const churning = t >= STATIC_END && t < CHURN_END;
  const resolved =
    t >= CHURN_END ? Math.floor(((t - CHURN_END) / (1 - CHURN_END)) * characters.length) : 0;
  return characters.map((character, index) => {
    if (character === " ") return { glyph: character, resolved: true, accent: false, mix: 0 };
    if (index < resolved) return { glyph: character, resolved: true, accent: false, mix: 0 };
    if (churning) {
      return { glyph: randomCell(), resolved: false, accent: false, mix: rippleMix(index, now) };
    }
    const cell = settled[index];
    return {
      glyph: cell.glyph,
      resolved: false,
      accent: cell.accent,
      mix: cell.accent ? pulseMix(index, now) : rippleMix(index, now),
    };
  });
}

/** A wait, spelled. The label arrives as the braille that spells it, churns, and resolves left to
 *  right — the wait a message states, in place of the `Loading` mark or a skeleton rather than
 *  beside one.
 *
 *  The decode runs once, when the wait changes. A step that stays put has already been read, so the
 *  resolved words shimmer instead of churning again — the movement says the step is still running
 *  without asking to be re-read. `loop` restores the repeating cycle; `delay` staggers a line
 *  against the ones above it; `color` drops the two channels for a dense or low-priority context,
 *  leaving the glyphs in the resting tone.
 *
 *  Reduced motion renders the words and starts no timer at all, and a hidden tab stops the one that
 *  is running: a line nobody is watching does not churn. */
export function DecodeLine({
  text,
  delay = 0,
  loop = false,
  color = true,
  className,
}: {
  text: string;
  delay?: number;
  loop?: boolean;
  color?: boolean;
  className?: string;
}) {
  const still = useReducedMotion();
  const settled = useMemo(() => settle(text), [text]);
  const [shown, setShown] = useState(text);
  const [tick, setTick] = useState(0);
  const frames = useRef(0);

  /** A new wait glyphs in from the churn rather than inheriting how far the one before it had got,
   *  which would show a stranger's words already half resolved. */
  if (shown !== text) {
    setShown(text);
    setTick(CHURN_FRAME);
    frames.current = CHURN_FRAME;
  }

  useEffect(() => {
    if (still) return;
    let timer: number | undefined;
    let spent = false;
    const advance = () => {
      frames.current += 1;
      if (!loop && frames.current * FRAME_MS >= DECODE_MS) {
        spent = true;
        window.clearInterval(timer);
      }
      setTick(frames.current);
    };
    const start = () => {
      window.clearInterval(timer);
      if (!spent) timer = window.setInterval(advance, FRAME_MS);
    };
    const opening = window.setTimeout(start, delay);
    const watch = () => (document.hidden ? window.clearInterval(timer) : start());
    document.addEventListener("visibilitychange", watch);
    return () => {
      window.clearTimeout(opening);
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", watch);
    };
  }, [still, text, delay, loop]);

  if (still) return <span className={className}>{text}</span>;

  const elapsed = tick * FRAME_MS;
  const t = Math.min(1, (loop ? elapsed % (DECODE_MS + HOLD_MS) : elapsed) / DECODE_MS);
  return (
    <span className={className}>
      <span className="sr-only">{text}</span>
      <Cells cells={decodeFrame(text, t, elapsed / 1_000, settled)} color={color} />
    </span>
  );
}

/** A column one character wide only holds glyphs a face gives one width to: in a proportional face
 *  a braille cell out of a fallback and an `m` beside it spill their columns and collide. */
function Cells({ cells, color }: { cells: DecodeCell[]; color: boolean }) {
  const words: { cell: DecodeCell; at: number }[][] = [[]];
  cells.forEach((cell, at) => {
    if (cell.glyph === " ") words.push([]);
    else words[words.length - 1].push({ cell, at });
  });
  return (
    <span aria-hidden data-slot="decode-text" className="font-mono text-small">
      {words.map((word, index) => (
        <span key={index}>
          {index > 0 ? " " : null}
          <span className="inline-block whitespace-pre">
            {word.map(({ cell, at }) => (
              <span
                key={at}
                data-slot="decode-cell"
                data-resolved={cell.resolved ? "" : undefined}
                data-accent={cell.accent ? "" : undefined}
                style={
                  color && !cell.resolved
                    ? ({ "--f": cell.mix.toFixed(1) } as CSSProperties)
                    : undefined
                }
                className={cn(
                  "inline-block w-(--size-decode-cell) text-center",
                  cell.resolved
                    ? "font-medium text-foreground"
                    : color
                      ? cell.accent
                        ? "decode-pulse"
                        : "decode-ripple"
                      : "text-muted-foreground",
                )}
              >
                {cell.glyph}
              </span>
            ))}
          </span>
        </span>
      ))}
    </span>
  );
}

export function useReducedMotion(): boolean {
  const [still, setStill] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const answer = () => setStill(query.matches);
    query.addEventListener("change", answer);
    return () => query.removeEventListener("change", answer);
  }, []);
  return still;
}
