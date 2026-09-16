import { resetStreams, setReattachTimer } from "@/lib/turnStream";
import {
  ANSWERED,
  PACES,
  SIMULATED_TURN,
  type Beat,
  type Pace,
  type Scenario,
} from "@/playground/simulator/scenarios";

const CONNECTING = 0;
const OPEN = 1;
const CLOSED = 2;

const STOP_HEADER = "x-ufo-stop-turn";
const ANSWER_HEADER = "x-ufo-answer-turn";

const REATTACH_MS = 40;

const FOLLOW_UPS = [
  {
    kind: "ask",
    hook: "Compare the two entry plans",
    prompt: "Compare their entry plan to ours, line by line.",
  },
  {
    kind: "watch",
    hook: "Watch their changelog",
    prompt: "Watch their changelog and tell me when the pricing moves.",
  },
];

let armed: Beat[] = [];
let factor = 1;
let dropped = false;
let opened: SimulatedStream | null = null;

function pause(ms: number): Promise<void> {
  return new Promise((wake) => setTimeout(wake, ms));
}

type Listener = (event: MessageEvent) => void;

/* A drop stands until the next scenario is armed, so the six reattaches it provokes fail at once
   rather than replaying the turn six more times. */
class SimulatedStream {
  static readonly CLOSED = CLOSED;
  readonly url: string;
  readyState = CONNECTING;
  onmessage: Listener | null = null;
  onerror: (() => void) | null = null;
  private readonly listeners = new Map<string, Listener[]>();
  private done = false;

  constructor(url: string) {
    this.url = url;
    opened = this;
    queueMicrotask(() => void this.play(armed));
  }

  addEventListener(name: string, handler: Listener): void {
    this.listeners.set(name, (this.listeners.get(name) ?? []).concat(handler));
  }

  removeEventListener(name: string, handler: Listener): void {
    const kept = (this.listeners.get(name) ?? []).filter((held) => held !== handler);
    this.listeners.set(name, kept);
  }

  close(): void {
    this.done = true;
    this.readyState = CLOSED;
  }

  cut(): void {
    this.done = true;
    this.deliver("terminal", { status: "cancelled" });
  }

  private async play(beats: readonly Beat[]): Promise<void> {
    if (dropped) {
      this.fail();
      return;
    }
    this.readyState = OPEN;
    this.deliver("open", null);
    for (const beat of beats) {
      if (factor) await pause(beat.wait * factor);
      if (this.done) return;
      if ("drop" in beat) {
        dropped = true;
        this.fail();
        return;
      }
      this.deliver(beat.event, beat.data);
    }
  }

  private fail(): void {
    this.done = true;
    this.readyState = CLOSED;
    this.onerror?.();
  }

  private deliver(name: string, data: unknown): void {
    const event = new MessageEvent(name, { data: JSON.stringify(data) });
    if (name === "message") {
      this.onmessage?.(event);
      return;
    }
    for (const handler of this.listeners.get(name) ?? []) handler(event);
  }
}

function posted(init: RequestInit): Response {
  const headers = new Headers(init.headers);
  if (headers.get(STOP_HEADER)) {
    opened?.cut();
    return Response.json({ stopped: true });
  }
  if (headers.get(ANSWER_HEADER)) {
    armed = ANSWERED;
    dropped = false;
    return Response.json({ turn_id: SIMULATED_TURN });
  }
  return Response.json({ turn_id: SIMULATED_TURN, opened_run: true });
}

function answered(path: string, init?: RequestInit): Response {
  if (init?.method === "POST") return posted(init);
  if (path.endsWith("/transcript")) return Response.json({ messages: [] });
  if (path.endsWith("/follow-ups")) return Response.json({ offers: FOLLOW_UPS, ranking: false });
  if (path.endsWith("/starters")) return Response.json({ starters: [], unlock: null });
  if (path.endsWith("/status")) return Response.json({ statuses: [], out_of_credit: false });
  throw new Error("The simulator answers no read of " + path);
}

const simulated: typeof fetch = (input, init) => {
  const url = input instanceof Request ? input.url : String(input);
  return Promise.resolve(answered(url.split("?")[0], init));
};

const reattach = (fn: () => void) => setTimeout(fn, REATTACH_MS * factor);

/** Stub `fetch` and `EventSource` for this page, and hand `turnStream` a reattach delay a reader
 *  can sit through. The simulator is its own entry, so nothing of the portal runs beside it.
 *  Returns the restore. */
export function installWire(): () => void {
  const heldFetch = window.fetch;
  const heldStream = window.EventSource;
  window.fetch = simulated;
  window.EventSource = SimulatedStream as unknown as typeof EventSource;
  setReattachTimer(reattach);
  return () => {
    clearWire();
    window.fetch = heldFetch;
    window.EventSource = heldStream;
  };
}

/** The frames the next stream plays. Every later send replays them until another is armed. */
export function armScenario(scenario: Scenario): void {
  armed = scenario.beats;
  dropped = false;
}

export function holdPace(pace: Pace): void {
  const held = PACES.find((option) => option.pace === pace);
  if (!held) throw new Error("The simulator has no pace named " + pace);
  factor = held.factor;
}

/** Close the open stream, drop every reattach it scheduled, and forget the drop it ended on. */
export function clearWire(): void {
  resetStreams();
  setReattachTimer(reattach);
  opened = null;
  dropped = false;
}
