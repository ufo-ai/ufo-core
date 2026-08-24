import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";
import { framedNavigation, type WorkspacePlace } from "@/lib/route";
import { navigate } from "@/lib/router";
import type { Crumb } from "@/lib/title";
import type { Agent, Member } from "@/lib/types";

/** The portal shell's side of the app bridge (RFC 0039, `docs/apps-prototype-contracts.md`
 *  Contract 1). An app's homepage is a page the app's agent built, framed cross-origin from the
 *  portal, so it cannot call the portal API itself. The shell hosting the frame proxies the
 *  member's own surface to it through one generic verb — `call`, a method and a path matched
 *  against the endpoint table below — and drives portal navigation on its behalf, all over
 *  `postMessage`. The protocol never names a capability: adding one is adding an endpoint row,
 *  and the page-facing conveniences live in the client, not here.
 *
 *  Prototype trust (deferred, documented in RFC 0039's security debt): the shell acts on the
 *  frame's requests under the viewer's own session with no per-message gate, and replies are posted
 *  with a `*` target origin. The endpoint table and the route table's own frame column are the only
 *  fences, and they bound what the frame can reach, not who authored it. */

type BodyKind = "json" | "text" | "urlencoded";

type Endpoint = { method: "GET" | "POST"; template: string[]; body?: BodyKind; stream?: boolean };

/** The portal endpoints the shell forwards, as method + segment templates; a `{...}` segment
 *  matches any one non-empty, non-traversing path segment, and the query string rides through
 *  untouched. `body` names the encoding a POST row takes. Extend the surface a frame can reach
 *  only by adding a row here. */
const ENDPOINTS: Endpoint[] = (
  [
    ["GET", "api/agents"],
    ["GET", "api/agents/status"],
    ["GET", "api/chats"],
    ["GET", "workspace/starters"],
    ["GET", "objects/{kind}"],
    ["GET", "objects/{kind}/{name}"],
    ["GET", "agents/{id}/conversations"],
    ["GET", "agents/{id}/transcript"],
    ["GET", "agents/{id}/conversations/{cid}/transcript"],
    ["GET", "agents/{id}/conversations/{cid}/slots"],
    ["GET", "agents/{id}/conversations/{cid}/slots/{slot}"],
    ["POST", "objects/{kind}", "json"],
    ["POST", "objects/{kind}/{name}/delete", "json"],
    ["POST", "agents/{id}/chat", "text"],
    ["POST", "agents/{id}/intents", "json"],
    ["POST", "credentials", "urlencoded"],
  ] as const
).map(([method, template, body]) => ({ method, template: template.split("/"), body }));

/** The intent verbs a frame may post: the object mutations the kernel's own record panes speak
 *  (`apply`, `delete` — the same authority the table's direct-write rows already grant) and the
 *  rebuilds the shipped pages carry as controls. The intents lane reaches acts far past a page's
 *  remit — membership, credentials, deploys — and a frame speaks with the viewer's whole session,
 *  so the fence names the verbs rather than trusting the page (RFC 0039 security debt: the
 *  per-message gate is deferred, the table is the fence). */
const FRAME_INTENT_VERBS: ReadonlySet<string> = new Set([
  "apply",
  "delete",
  "rebuild_reports",
  "rebuild_page_facts",
]);

const INTENTS_TEMPLATE = "agents/{id}/intents";

const CHAT_TEMPLATE = "agents/{id}/chat";

/** The one server-sent-event endpoint: a turn's live frames. A `call` matching a stream row is
 *  answered with `frame` messages as events arrive and one `end` when the stream closes, instead
 *  of a single `data` reply; a `close` message with the call's id ends it early. */
const STREAM_ENDPOINT: Endpoint = {
  method: "GET",
  template: "turns/{id}/stream".split("/"),
  stream: true,
};
ENDPOINTS.push(STREAM_ENDPOINT);

/** How many live turn streams one frame may hold open at once — a page tails the turns it is
 *  watching, not every turn it ever saw. */
const STREAM_CAP = 8;

type BridgeMessage =
  | { ufo: "ready" }
  | {
      ufo: "call";
      id: string;
      method: string;
      path: string;
      body?: unknown;
      /** A multipart body as its parts: `FormData` cannot ride `postMessage`, but its entries —
       *  strings and Files — structured-clone whole, so the page decomposes and the shell
       *  reassembles. Only the chat admit takes one. */
      form?: unknown;
      headers?: Record<string, string>;
    }
  | { ufo: "close"; id: string }
  | { ufo: "navigate"; to: string }
  | { ufo: "founded"; agent_id: string; conversation_id: string; title: string }
  | { ufo: "resize"; height: number };

/** The one header namespace a call may carry through: the surface's own `x-ufo-*` controls — the
 *  timezone, a stop, a question's answer. Anything else a page states is dropped, so a frame
 *  cannot smuggle a header the member's own portal would never send. */
const HEADER_PREFIX = "x-ufo-";

function carriedHeaders(stated: unknown): Record<string, string> {
  if (typeof stated !== "object" || stated === null) return {};
  return Object.fromEntries(
    Object.entries(stated as Record<string, unknown>).filter(
      ([name, value]) => name.toLowerCase().startsWith(HEADER_PREFIX) && typeof value === "string",
    ),
  ) as Record<string, string>;
}

export type BridgeConfig = {
  iframe: HTMLIFrameElement;
  member: Member;
  agents: Agent[];
  agentId: string;
  /** The place the page stands at, handed to it in `init` so a sidebar or search landing opens the
   *  tile it named. It is the whole record the address carries, not one field of it: a page holds a
   *  place the way a portal tab does, and a frame handed one key of it could only stand the screen
   *  the member asked for by guessing the rest. */
  place?: WorkspacePlace;
  /** Where the page itself stands in the portal, as the trail names it. The page draws it as the
   *  crumb over its own band whenever that band names something the shell never read — a report, a
   *  member's page — because the shell's trail ends at the page and the band stands one step past
   *  it. It rides `init` and no further: the frame that outlives a place change does not outlive the
   *  screen this crumb names. */
  crumb?: Crumb;
  /** A conversation the page's own send founded, told to the shell so the rail carries the row
   *  without waiting for its next read. The page could only have founded it through this bridge's
   *  own chat call, so the report claims nothing the shell did not broker. */
  onCreated?: (agentId: string, conversationId: string, title: string) => void;
  /** Whether this frame is the chat surface — the one page whose job is speaking. Only it may
   *  post the chat admit: a send runs a model turn with the viewer's whole authority, and every
   *  other app's page has the shell-owned right-side chat for talking, so its frame gets no voice
   *  of its own to abuse on load. */
  chatSurface?: boolean;
};

/** The attached bridge: `detach` releases it when the frame unmounts, and `place` follows the
 *  pane's place while the frame stays mounted — the page got its `init` once, so a place that
 *  changes afterwards is posted as its own message and carried into any `init` still to come. */
export type BridgeHandle = {
  detach: () => void;
  place: (place: WorkspacePlace) => void;
};

/** The endpoint row a method and path resolve to, or null where the table admits neither. */
export function endpointFor(method: string, path: string): Endpoint | null {
  const segments = path
    .split("?")[0]
    .replace(/^\/+/, "")
    .split("/");
  return (
    ENDPOINTS.find(
      (endpoint) =>
        endpoint.method === method &&
        endpoint.template.length === segments.length &&
        endpoint.template.every((part, index) => {
          const segment = segments[index];
          if (part.startsWith("{")) return segment.length > 0 && !segment.includes("..");
          return part === segment;
        }),
    ) ?? null
  );
}

function isBridgeMessage(data: unknown): data is BridgeMessage {
  return (
    typeof data === "object" &&
    data !== null &&
    typeof (data as { ufo?: unknown }).ufo === "string"
  );
}

/** The one shape a call's body may take for its row: a JSON object for a `json` row — as the
 *  object itself or as the string a page's own fetch serialized it to — a non-empty string for a
 *  `text` row, nothing for a row that takes none. Returns the refusal, or null. */
function bodyFault(endpoint: Endpoint, body: unknown): string | null {
  switch (endpoint.body) {
    case "json":
      if (typeof body !== "object" || body === null || Array.isArray(body)) {
        return "This endpoint takes a JSON object body.";
      }
      return null;
    case "text":
      if (body === undefined || body === null) return null;
      if (typeof body !== "string" || !body.trim()) {
        return "This endpoint takes a non-empty text body.";
      }
      return null;
    case "urlencoded":
      if (typeof body !== "string" || !body.trim()) {
        return "This endpoint takes a form body.";
      }
      return null;
    case undefined:
      return body === undefined || body === null ? null : "This endpoint takes no body.";
  }
}

/** Attach the shell side of the bridge to one homepage iframe. Returns a detach function; call it
 *  when the frame unmounts (a redeploy remounts it under a new key, so each frame gets its own
 *  attach/detach). The frame announces itself with `ready`; the shell replies `init`. Because the
 *  frame may post `ready` before this listener is installed, its client retries `ready` until an
 *  `init` arrives. Every `call` carrying a string `id` is answered with exactly one `data` reply —
 *  a malformed field answers `ok: false` rather than silence, so a client promise never waits
 *  forever; only a message with no usable `id` has nothing to correlate a reply to. */
export function attachBridge({
  iframe,
  member,
  agents,
  agentId,
  place,
  crumb,
  onCreated,
  chatSurface = false,
}: BridgeConfig): BridgeHandle {
  const streams = new Map<string, AbortController>();
  let standing: WorkspacePlace = place ?? {};
  let page: MessageEventSource | null = null;

  /** Relay one server-sent-event response as `frame` messages, then one `end`. A minimal SSE
   *  parse — `event:`/`data:` lines, events split on blank lines — because the consumer is the
   *  page's own handler, not an EventSource: named events can't be wildcarded there, and the
   *  producer is this deploy's own turn stream. */
  async function relay(
    id: string,
    path: string,
    reply: (payload: unknown) => void,
  ): Promise<void> {
    const control = new AbortController();
    streams.set(id, control);
    try {
      const res = await fetch(BASE + path, {
        credentials: "same-origin",
        signal: control.signal,
      });
      if (!res.ok || !res.body) {
        reply({ ufo: "end", id, error: "Error " + res.status });
        return;
      }
      reply({ ufo: "opened", id });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let held = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        held += decoder.decode(value, { stream: true });
        const events = held.split("\n\n");
        held = events.pop() ?? "";
        for (const block of events) {
          let event = "message";
          const data: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
          }
          if (data.length) reply({ ufo: "frame", id, event, data: data.join("\n") });
        }
      }
      reply({ ufo: "end", id });
    } catch {
      reply({ ufo: "end", id, error: "The stream ended." });
    } finally {
      streams.delete(id);
    }
  }

  async function handle(message: BridgeMessage, source: MessageEventSource): Promise<void> {
    const reply = (payload: unknown) => source.postMessage(payload, { targetOrigin: "*" });

    switch (message.ufo) {
      case "ready":
        reply({
          ufo: "init",
          member: { email: member.email, admin: member.admin },
          agents,
          agentId,
          place: standing,
          crumb:
            crumb?.at && framedNavigation(crumb.at)
              ? { ...crumb, at: new URL(crumb.at, location.origin + BASE).href }
              : crumb,
          portal: location.origin,
        });
        return;
      case "call": {
        if (typeof message.id !== "string" || !message.id) return;
        const refuse = (error: string) => reply({ ufo: "data", id: message.id, ok: false, error });
        if (typeof message.method !== "string" || typeof message.path !== "string") {
          refuse("A call names a method and a path.");
          return;
        }
        const endpoint = endpointFor(message.method, message.path);
        if (endpoint === null) {
          refuse("This endpoint is not available.");
          return;
        }
        if (endpoint.method === "POST" && endpoint.template.join("/") === CHAT_TEMPLATE && !chatSurface) {
          refuse("Only the chat surface sends messages.");
          return;
        }
        let body = message.body;
        let form: FormData | null = null;
        if (message.form !== undefined) {
          const parts = message.form;
          if (
            endpoint.body !== "text" ||
            body !== undefined ||
            !Array.isArray(parts) ||
            parts.some(
              (part) =>
                typeof part !== "object" ||
                part === null ||
                typeof (part as { name?: unknown }).name !== "string" ||
                !(
                  typeof (part as { value?: unknown }).value === "string" ||
                  (part as { value?: unknown }).value instanceof Blob
                ),
            )
          ) {
            refuse("This endpoint takes no attachments.");
            return;
          }
          form = new FormData();
          for (const part of parts as { name: string; value: string | Blob }[]) {
            form.append(part.name, part.value);
          }
        }
        if (endpoint.body === "json" && typeof body === "string") {
          try {
            body = JSON.parse(body);
          } catch {
            refuse("This endpoint takes a JSON object body.");
            return;
          }
        }
        if (form === null) {
          const fault = bodyFault(endpoint, body);
          if (fault !== null) {
            refuse(fault);
            return;
          }
        }
        if (endpoint.template.join("/") === INTENTS_TEMPLATE) {
          const verb = (body as { verb?: unknown } | null)?.verb;
          if (typeof verb !== "string" || !FRAME_INTENT_VERBS.has(verb)) {
            refuse("This intent is not available from an app page.");
            return;
          }
        }
        const path = message.path.startsWith("/") ? message.path : "/" + message.path;
        if (endpoint.stream) {
          if (streams.size >= STREAM_CAP) {
            refuse("Too many open streams — close one first.");
            return;
          }
          void relay(message.id, path, reply);
          return;
        }
        try {
          const carried = carriedHeaders(message.headers);
          const res = await fetch(BASE + path, {
            method: endpoint.method,
            credentials: "same-origin",
            headers: {
              ...carried,
              ...(form === null && endpoint.body === "json"
                ? { "content-type": "application/json" }
                : {}),
              ...(form === null && endpoint.body === "text" && typeof body === "string"
                ? { "content-type": "text/plain; charset=utf-8" }
                : {}),
              ...(form === null && endpoint.body === "urlencoded"
                ? { "content-type": "application/x-www-form-urlencoded;charset=UTF-8" }
                : {}),
            },
            ...(form !== null ? { body: form } : {}),
            ...(form === null && endpoint.body === "json" ? { body: JSON.stringify(body) } : {}),
            ...(form === null && endpoint.body === "text" && typeof body === "string"
              ? { body }
              : {}),
            ...(form === null && endpoint.body === "urlencoded" && typeof body === "string"
              ? { body }
              : {}),
          });
          reply({
            ufo: "data",
            id: message.id,
            ok: res.ok,
            status: res.status,
            body: await res.text().catch(() => ""),
            refusal: res.headers.get(REFUSAL_HEADER),
            fault: res.headers.get(SESSION_FAULT_HEADER),
          });
        } catch {
          refuse("Network error — try again.");
        }
        return;
      }
      case "close":
        if (typeof message.id === "string") streams.get(message.id)?.abort();
        return;
      /* A page's navigation is a navigation like any other, so the router writes it: the drawer over
         the page shuts and the arrival lands, where a bare hash write left the shell to catch up a
         task later. */
      case "navigate":
        if (typeof message.to === "string" && framedNavigation(message.to)) navigate(message.to);
        return;
      case "founded":
        if (
          typeof message.agent_id === "string" &&
          typeof message.conversation_id === "string" &&
          typeof message.title === "string"
        ) {
          onCreated?.(message.agent_id, message.conversation_id, message.title);
        }
        return;
      case "resize":
        if (typeof message.height === "number" && message.height > 0) {
          iframe.style.height = message.height + "px";
        }
        return;
    }
  }

  const onMessage = (event: MessageEvent) => {
    if (!iframe.contentWindow || event.source !== iframe.contentWindow) return;
    if (!isBridgeMessage(event.data)) return;
    page = event.source;
    void handle(event.data, event.source);
  };
  window.addEventListener("message", onMessage);
  return {
    detach: () => {
      window.removeEventListener("message", onMessage);
      for (const control of streams.values()) control.abort();
      streams.clear();
      page = null;
    },
    place: (next: WorkspacePlace) => {
      standing = next;
      page?.postMessage({ ufo: "place", place: next }, { targetOrigin: "*" });
    },
  };
}
