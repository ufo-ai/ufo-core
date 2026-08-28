import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";
import type { WorkspacePlace } from "@/lib/route";
import type { Crumb } from "@/lib/title";
import type { Agent } from "@/lib/types";
import {
  beginLifecycleWork,
  cancelLifecycleWork,
  reviseLifecycleWork,
  setNativeTimeout,
  settleLifecycleWork,
} from "@/apps/lifecycle";

/** The app page's side of the bridge (RFC 0039, `docs/apps-prototype-contracts.md` Contract 1) and
 *  the whole of what a page needs from the portal: the page is a built bundle that imports this
 *  module with the rest of the kit, so nothing is fetched off the portal to make it run. `connect`
 *  performs the ready/init handshake, and `installShims` reroutes the page's `fetch` and
 *  `EventSource` for `BASE`-prefixed paths through the shell's `call` verb — `lib/api`,
 *  `lib/turnStream`, and every kernel read work verbatim, and the page never speaks the protocol
 *  itself. Everything else — the page's own assets, absolute signed artifact links — keeps the
 *  native path. */

export type AppInit = {
  member: { email: string; admin: boolean };
  agents?: Agent[];
  agentId: string;
  /** The place the pane opened the page at, whole: the same record a portal tab stands on, so a page
   *  reads its screen off the address the member arrived with rather than off one field of it. */
  place: WorkspacePlace;
  /** Where this page stands in the portal, as the shell's own trail names it: the label and the
   *  address it stands at. The page's band draws it as its crumb whenever that band names something
   *  deeper than the page — a report, a member's page — so where the member is reads the same inside
   *  the frame as the tab title says outside it. One band per page: the crumb joins the page's own
   *  band rather than restoring a band above it. */
  crumb?: Crumb;
  /** The portal's origin, which a page names an artifact of another conversation from. */
  portal: string;
};

type DataReply = {
  ufo: "data";
  id: string;
  ok: boolean;
  status?: number;
  body?: string;
  error?: string;
  refusal?: string | null;
  fault?: string | null;
};

type StreamHandlers = {
  lease: number;
  opened: () => void;
  frame: (event: string, data: string) => void;
  end: (error?: string) => void;
};

const READY_RETRY_MS = 100;
const READY_ATTEMPTS = 30;

let counter = 0;
const CALLS = new Map<string, (reply: DataReply) => void>();
const STREAMS = new Map<string, StreamHandlers>();
const PLACE_LISTENERS = new Set<(place: WorkspacePlace) => void>();
let init: AppInit | null = null;
let announce: ((init: AppInit) => void) | null = null;

function send(message: object): void {
  window.top?.postMessage(message, "*");
}

window.addEventListener("message", (event: MessageEvent) => {
  const message = event.data as { ufo?: string; id?: string } | null;
  if (!message || typeof message !== "object" || typeof message.ufo !== "string") return;
  switch (message.ufo) {
    case "init":
      if (init) return;
      init = event.data as AppInit;
      announce?.(init);
      return;
    case "data": {
      const reply = event.data as DataReply;
      const call = CALLS.get(reply.id);
      if (call) {
        CALLS.delete(reply.id);
        call(reply);
        return;
      }
      if (!reply.ok) {
        const stream = STREAMS.get(reply.id);
        STREAMS.delete(reply.id);
        stream?.end(reply.error ?? reply.body ?? "stream refused");
      }
      return;
    }
    case "place": {
      const place = (event.data as { place?: WorkspacePlace }).place ?? {};
      for (const listener of PLACE_LISTENERS) listener(place);
      return;
    }
    case "opened":
      if (typeof message.id === "string") {
        const stream = STREAMS.get(message.id);
        if (stream) {
          reviseLifecycleWork(stream.lease);
          stream.opened();
        }
      }
      return;
    case "frame": {
      const frame = event.data as { id: string; event: string; data: string };
      const stream = STREAMS.get(frame.id);
      if (stream) {
        reviseLifecycleWork(stream.lease);
        stream.frame(frame.event, frame.data);
      }
      return;
    }
    case "end": {
      const ended = event.data as { id: string; error?: string };
      const stream = STREAMS.get(ended.id);
      STREAMS.delete(ended.id);
      stream?.end(ended.error);
      return;
    }
  }
});

/** The handshake: announce `ready` until the shell's `init` arrives. The shell attaches its
 *  listener when the pane mounts, so a frame whose bundle evaluated first would lose a single
 *  `ready` — hence the retries. They are bounded because a page nothing frames would otherwise
 *  post forever, and the wait is not: an `init` arriving after the last `ready` still mounts the
 *  page, so a shell slower than the budget costs a page nothing. A page nothing frames stays
 *  unmounted, which is all there is to draw without a shell to read through. */
export function connect(): Promise<AppInit> {
  return new Promise((resolve) => {
    if (init) {
      resolve(init);
      return;
    }
    announce = resolve;
    let attempts = 0;
    const retry = () => {
      if (init || attempts++ >= READY_ATTEMPTS) return;
      send({ ufo: "ready" });
      setNativeTimeout(retry, READY_RETRY_MS);
    };
    retry();
  });
}

/** Move the portal to an address: the page asks the shell framing it to go there, so a link inside
 * an app lands on a portal screen rather than inside the frame. */
export function navigate(to: string): void {
  send({ ufo: "navigate", to });
}

/** Hand words to the composer of a new chat with this app, unsent.

 *  The member reads them and presses send themselves. A press that spends a turn without the member
 *  reading it is a surprise, and an ask that turns a feature on binds what it grants to whoever
 *  speaks it — so the member has to be in that conversation rather than merely the cause of one.
 *  The frame could not send it anyway: the bridge admits `POST agents/{id}/chat` from the chat
 *  surface alone, and that fence does not move for this. */
export function compose(text: string): void {
  send({ ufo: "compose", text });
}

/** Tell the shell a send on this page founded a conversation, so its rail carries the row without
 *  waiting for the next read. */
export function founded(agentId: string, conversationId: string, title: string): void {
  send({ ufo: "founded", agent_id: agentId, conversation_id: conversationId, title });
}

/** The pane's place as it changes while the page stands — the live half of `init`'s `place`. Returns
 *  the unsubscribe. */
export function onPlaced(listener: (place: WorkspacePlace) => void): () => void {
  PLACE_LISTENERS.add(listener);
  return () => {
    PLACE_LISTENERS.delete(listener);
  };
}

function call(
  method: string,
  path: string,
  body?: unknown,
  headers?: Record<string, string>,
  form?: { name: string; value: string | File }[],
): Promise<DataReply> {
  const id = "r" + ++counter;
  const lease = beginLifecycleWork("unary");
  return new Promise((resolve) => {
    CALLS.set(id, (reply) => {
      resolve(reply);
      settleLifecycleWork(lease);
    });
    send({
      ufo: "call",
      id,
      method,
      path,
      ...(body === undefined ? {} : { body }),
      ...(form === undefined ? {} : { form }),
      ...(headers && Object.keys(headers).length ? { headers } : {}),
    });
  });
}

function statedHeaders(stated: HeadersInit | undefined): Record<string, string> {
  if (!stated) return {};
  if (stated instanceof Headers) return Object.fromEntries(stated.entries());
  if (Array.isArray(stated)) return Object.fromEntries(stated);
  return { ...stated };
}

function tunneled(reply: DataReply): Response {
  if (reply.status === undefined) {
    return new Response(reply.error ?? "request refused", {
      status: 400,
      headers: { [REFUSAL_HEADER]: "bridge" },
    });
  }
  const headers = new Headers();
  if (reply.refusal) headers.set(REFUSAL_HEADER, reply.refusal);
  if (reply.fault) headers.set(SESSION_FAULT_HEADER, reply.fault);
  return new Response(reply.body ?? "", { status: reply.status, headers });
}

/** `EventSource` over the bridge's stream relay, faithful where `turnStream` depends on it: `open`
 *  fires when the shell's relay connected, named frames dispatch as message events, and a stream
 *  ending for any reason the page did not ask for closes the source and fires `error` — exactly
 *  the signal the reattach logic acts on. */
class BridgeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readyState: number = BridgeEventSource.CONNECTING;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  private id: string;

  constructor(url: string) {
    super();
    if (!url.startsWith(BASE + "/")) throw new Error("only portal streams reach the bridge");
    this.id = "s" + ++counter;
    const lease = beginLifecycleWork("stream");
    STREAMS.set(this.id, {
      lease,
      opened: () => {
        this.readyState = BridgeEventSource.OPEN;
        const event = new Event("open");
        this.onopen?.(event);
        this.dispatchEvent(event);
      },
      frame: (name, data) => {
        const event = new MessageEvent(name, { data });
        if (name === "message") this.onmessage?.(event);
        this.dispatchEvent(event);
      },
      end: () => {
        this.readyState = BridgeEventSource.CLOSED;
        const event = new Event("error");
        try {
          this.onerror?.(event);
          this.dispatchEvent(event);
        } finally {
          settleLifecycleWork(lease);
        }
      },
    });
    send({ ufo: "call", id: this.id, method: "GET", path: url.slice(BASE.length) });
  }

  close(): void {
    if (this.readyState === BridgeEventSource.CLOSED) return;
    this.readyState = BridgeEventSource.CLOSED;
    const stream = STREAMS.get(this.id);
    STREAMS.delete(this.id);
    if (stream) cancelLifecycleWork(stream.lease);
    send({ ufo: "close", id: this.id });
  }
}

/** Reroute the page's portal transport: a fetch of a `BASE`-prefixed path and a turn stream's
 *  `EventSource` ride the bridge; everything else — the page's own assets, absolute signed
 *  artifact links — keeps the native path. */
export function installShims(): void {
  const native = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, options?: RequestInit): Promise<Response> => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.startsWith(BASE + "/")) return native(input, options);
    const method = options?.method ?? "GET";
    if (options?.body instanceof URLSearchParams) {
      return tunneled(
        await call(
          method,
          url.slice(BASE.length),
          options.body.toString(),
          statedHeaders(options?.headers),
        ),
      );
    }
    if (options?.body instanceof FormData) {
      const parts = [...options.body.entries()].map(([name, value]) => ({ name, value }));
      return tunneled(
        await call(method, url.slice(BASE.length), undefined, statedHeaders(options?.headers), parts),
      );
    }
    if (options?.body !== undefined && typeof options.body !== "string") {
      return new Response("This page sends text only.", {
        status: 400,
        headers: { [REFUSAL_HEADER]: "bridge" },
      });
    }
    return tunneled(
      await call(method, url.slice(BASE.length), options?.body, statedHeaders(options?.headers)),
    );
  };
  (window as { EventSource: unknown }).EventSource = BridgeEventSource;
}
