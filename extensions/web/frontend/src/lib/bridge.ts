import { BASE, REFUSAL_HEADER, SESSION_FAULT_HEADER } from "@/lib/api";
import { setPendingAsk } from "@/lib/pendingAsk";
import { framedNavigation, newChatHash, parseHash, type WorkspacePlace } from "@/lib/route";
import { navigate } from "@/lib/router";
import type { Crumb } from "@/lib/title";
import type { Agent, Member } from "@/lib/types";

/** Prototype trust, per RFC 0039's security debt: the shell acts on the frame's requests under the
 *  viewer's own session with no per-message gate, and replies are posted with a `*` target origin. */

type BodyKind = "json" | "multipart" | "text" | "urlencoded";

type Endpoint = { method: "GET" | "POST"; template: string[]; body?: BodyKind; stream?: boolean };

const ENDPOINTS: Endpoint[] = (
  [
    ["GET", "api/agents"],
    ["GET", "api/agents/status"],
    ["GET", "api/chats"],
    ["GET", "workspace/starters"],
    ["GET", "objects/{kind}"],
    ["GET", "objects/{kind}/{name}"],
    ["GET", "actions/{kind}"],
    ["GET", "actions/{kind}/{name}"],
    ["GET", "agents/{id}/conversations/{cid}/transcript"],
    ["GET", "agents/{id}/conversations/{cid}/slots"],
    ["GET", "agents/{id}/conversations/{cid}/slots/{slot}"],
    ["POST", "objects/{kind}", "json"],
    ["POST", "objects/{kind}/{name}/delete", "json"],
    ["POST", "agents/{id}/chat", "text"],
    ["POST", "agents/{id}/intents", "json"],
    ["POST", "agents/{id}/actions/{kind}/{action}", "json"],
    ["POST", "agents/{id}/actions/{kind}/{name}/{action}", "json"],
    ["POST", "credentials", "urlencoded"],
    ["POST", "preview", "multipart"],
  ] as const
).map(([method, template, body]) => ({ method, template: template.split("/"), body }));

/** Every admitted verb dispatches verbatim and runs no model round: a turn that runs rounds runs with
 *  the viewer's whole authority, so a frame gets no way to start one on load. */
const FRAME_INTENT_VERBS: ReadonlySet<string> = new Set([
  "apply",
  "delete",
  "connect",
]);

const INTENTS_TEMPLATE = "agents/{id}/intents";

const FRAME_HEADER = "x-ufo-frame";

const CHAT_TEMPLATE = "agents/{id}/chat";

const STREAM_ENDPOINT: Endpoint = {
  method: "GET",
  template: "turns/{id}/stream".split("/"),
  stream: true,
};
ENDPOINTS.push(STREAM_ENDPOINT);

const STREAM_CAP = 8;

const COMPOSE_MAX_CHARS = 2000;

type BridgeMessage =
  | { ufo: "ready" }
  | { ufo: "site-session-ended" }
  | {
      ufo: "call";
      id: string;
      method: string;
      path: string;
      body?: unknown;
      form?: unknown;
      headers?: Record<string, string>;
    }
  | { ufo: "close"; id: string }
  | { ufo: "navigate"; to: string }
  | { ufo: "compose"; text: string }
  | { ufo: "founded"; agent_id: string; conversation_id: string; title: string }
  | { ufo: "resize"; height: number };

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
  /** Read when `ready` is answered rather than held from attach, so a re-read reaches the page's next
   *  `init` without the bridge being rebound — a rebind aborts every stream the page holds open. */
  standing: () => {
    member: Member;
    agents: Agent[];
    crumb?: Crumb;
  };
  agentId: string;
  banded: boolean;
  place?: WorkspacePlace;
  onCreated?: (agentId: string, conversationId: string, title: string) => void;
  onConversation?: (conversationId: string) => void;
  onSessionEnded?: () => void;
  chatSurface?: boolean;
};

export type BridgeHandle = {
  detach: () => void;
  place: (place: WorkspacePlace) => void;
};

function segmentsOf(path: string): string[] {
  return path.split("?")[0].replace(/^\/+/, "").split("/");
}

export function endpointFor(method: string, path: string): Endpoint | null {
  const segments = segmentsOf(path);
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
    case "multipart":
      return "This endpoint takes a multipart body.";
    case undefined:
      return body === undefined || body === null ? null : "This endpoint takes no body.";
  }
}

export function attachBridge({
  iframe,
  standing,
  agentId,
  banded,
  place,
  onCreated,
  onConversation,
  onSessionEnded,
  chatSurface = false,
}: BridgeConfig): BridgeHandle {
  const streams = new Map<string, AbortController>();
  let placed: WorkspacePlace = place ?? {};
  let page: MessageEventSource | null = null;

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
      if (!control.signal.aborted) reply({ ufo: "end", id, error: "The stream ended." });
    } finally {
      streams.delete(id);
    }
  }

  async function handle(message: BridgeMessage, source: MessageEventSource): Promise<void> {
    const reply = (payload: unknown) => source.postMessage(payload, { targetOrigin: "*" });

    switch (message.ufo) {
      case "site-session-ended":
        onSessionEnded?.();
        return;
      case "ready": {
        const { member, agents, crumb } = standing();
        reply({
          ufo: "init",
          member: { email: member.email, admin: member.admin },
          agents,
          agentId,
          banded,
          place: placed,
          crumb:
            crumb?.at && framedNavigation(crumb.at)
              ? { ...crumb, at: new URL(crumb.at, location.origin + BASE).href }
              : crumb,
          portal: location.origin,
        });
        return;
      }
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
            (endpoint.body !== "text" && endpoint.body !== "multipart") ||
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
        const template = endpoint.template.join("/");
        if (template === INTENTS_TEMPLATE) {
          const verb = (body as { verb?: unknown } | null)?.verb;
          if (typeof verb !== "string" || !FRAME_INTENT_VERBS.has(verb)) {
            refuse("This intent is not available from an app page.");
            return;
          }
          // A grant binds to the app the page belongs to: the shell forwards under the viewer's whole session,
          // so a page naming another agent would bind that account there with no sign of it on the consent screen.
          const named = segmentsOf(message.path)[1];
          if (verb === "connect" && named !== agentId) {
            refuse("An app page connects an account to its own app.");
            return;
          }
          if (verb === "connect" && (body as { spec?: { shared?: unknown } }).spec?.shared) {
            refuse("An app page connects an account to itself alone.");
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
              [FRAME_HEADER]: "1",
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
      case "navigate": {
        if (typeof message.to !== "string" || !framedNavigation(message.to)) return;
        const asked = parseHash(message.to);
        if (onConversation && asked.kind === "chat" && asked.slot === undefined) {
          onConversation(asked.conversationId);
          return;
        }
        navigate(message.to);
        return;
      }
      case "compose":
        if (typeof message.text === "string" && message.text.trim()) {
          setPendingAsk(agentId, message.text.slice(0, COMPOSE_MAX_CHARS), false);
          navigate(newChatHash(agentId));
        }
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
      placed = next;
      page?.postMessage({ ufo: "place", place: next }, { targetOrigin: "*" });
    },
  };
}
