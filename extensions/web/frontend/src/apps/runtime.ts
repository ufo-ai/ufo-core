import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";
import type { WorkspacePlace } from "@/lib/route";

/** The app page's side of the bridge (RFC 0039, `docs/apps-prototype-contracts.md` Contract 1),
 *  shaped so the portal's own modules run in the page unchanged: `connect` performs the
 *  ready/init handshake, and `installShims` reroutes the page's `fetch` and `EventSource` for
 *  `BASE`-prefixed paths through the shell's `call` verb — `lib/api`, `lib/turnStream`, and every
 *  kernel read work verbatim, and the page never speaks the protocol itself. Everything else —
 *  the page's own assets, absolute signed artifact links — keeps the native path. */

export type AppInit = {
  member: { email: string; admin: boolean };
  agentId: string;
  /** The place the pane opened the page at, whole: the same record a portal tab stands on, so a page
   *  reads its screen off the address the member arrived with rather than off one field of it. */
  place: WorkspacePlace;
  /** The place's first lane — the single-record reading a page that stands on one open target
   *  takes, carried beside the whole record because a deployed page reads whichever of the two its
   *  source was written against, and the kit serves every deployed page. */
  open: string | null;
  /** The portal's origin, which is where the page's own loader fetched the kit from. */
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
      if (typeof message.id === "string") STREAMS.get(message.id)?.opened();
      return;
    case "frame": {
      const frame = event.data as { id: string; event: string; data: string };
      STREAMS.get(frame.id)?.frame(frame.event, frame.data);
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

/** The handshake: announce `ready` until the shell's `init` arrives. A page opened outside the
 *  shell settles as a rejection after the bounded retries rather than spinning forever. */
export function connect(): Promise<AppInit> {
  return new Promise((resolve, reject) => {
    if (init) {
      resolve(init);
      return;
    }
    announce = resolve;
    let attempts = 0;
    const retry = () => {
      if (init) return;
      if (attempts++ >= READY_ATTEMPTS) {
        announce = null;
        reject(new Error("no portal shell answered"));
        return;
      }
      send({ ufo: "ready" });
      setTimeout(retry, READY_RETRY_MS);
    };
    retry();
  });
}

export function navigate(to: string): void {
  send({ ufo: "navigate", to });
}

/** Tell the shell a send on this page founded a conversation, so its rail carries the row without
 *  waiting for the next read. */
export function founded(agentId: string, conversationId: string, title: string): void {
  send({ ufo: "founded", agent_id: agentId, conversation_id: conversationId, title });
}

/** The pane's place as it changes while the page stands — the live half of `init`'s `place`. Returns
 *  the unsubscribe. */
/** The place's first lane as it changes while the page stands — the single-target reading of
 *  `onPlaced`, for a page whose screen stands on one open record. Returns the unsubscribe. */
export function onOpenTarget(listener: (target: string | null) => void): () => void {
  return onPlaced((place) => listener(place.opens?.[0] ?? null));
}

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
  return new Promise((resolve) => {
    CALLS.set(id, resolve);
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
    STREAMS.set(this.id, {
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
        this.onerror?.(event);
        this.dispatchEvent(event);
      },
    });
    send({ ufo: "call", id: this.id, method: "GET", path: url.slice(BASE.length) });
  }

  close(): void {
    if (this.readyState === BridgeEventSource.CLOSED) return;
    this.readyState = BridgeEventSource.CLOSED;
    STREAMS.delete(this.id);
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
