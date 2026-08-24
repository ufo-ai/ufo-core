import { afterEach, expect, test, vi } from "vitest";

import {
  attachBridge as attach,
  endpointFor,
  type BridgeConfig,
  type BridgeHandle,
} from "@/lib/bridge";
import { BASE } from "@/lib/api";
import { framedNavigation } from "@/lib/route";
import { heldRoute } from "@/lib/router";

const MEMBER = { email: "member@example.com", admin: true };
const AGENT_ID = "11111111-1111-1111-1111-111111111111";
const AGENTS: BridgeConfig["agents"] = [
  { id: AGENT_ID, name: "Radar", model: "openai/gpt-5", main: false, icon: "radar" },
];
const CONVERSATION_ID = "22222222-2222-2222-2222-222222222222";
const PLACE = { q: "tokens", range: "90d", opens: [CONVERSATION_ID] };
const CRUMB = { label: "Radar", at: "#/agents/" + AGENT_ID };

/** A stand-in for the framed page's window: the shell compares sources by identity against
 *  `iframe.contentWindow` and replies to the source itself. */
function fakeFrame(): { iframe: HTMLIFrameElement; posted: unknown[] } {
  const posted: unknown[] = [];
  const contentWindow = { postMessage: (message: unknown) => void posted.push(message) };
  const iframe = { contentWindow, style: {} } as unknown as HTMLIFrameElement;
  return { iframe, posted };
}

/** Deliver a message to the shell as if it came from the frame's window. */
function deliver(data: unknown, source: unknown): void {
  const event = new MessageEvent("message", { data });
  Object.defineProperty(event, "source", { value: source });
  window.dispatchEvent(event);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body));
}

let bridge: BridgeHandle | null = null;

function attachBridge(config: Omit<BridgeConfig, "agents">): BridgeHandle {
  return attach({ ...config, agents: AGENTS });
}

afterEach(() => {
  bridge?.detach();
  bridge = null;
  vi.unstubAllGlobals();
  history.replaceState(null, "", location.pathname);
});

test("the endpoint table admits its rows and their fills, and nothing else", () => {
  expect(endpointFor("GET", "api/agents")).toBeTruthy();
  expect(endpointFor("GET", "objects/scheduled_task")).toBeTruthy();
  expect(endpointFor("GET", "objects/scheduled_task?paused=false")).toBeTruthy();
  expect(endpointFor("GET", "/agents/" + AGENT_ID + "/transcript")).toBeTruthy();
  expect(endpointFor("POST", "objects/scheduled_task")).toBeTruthy();
  expect(endpointFor("POST", "credentials")).toBeTruthy();
  expect(endpointFor("POST", "agents/" + AGENT_ID + "/chat?conversation=new")).toBeTruthy();
  expect(endpointFor("GET", "api/chats?conversation=" + AGENT_ID)).toBeTruthy();
  // Not a row, the wrong method for one, the wrong depth, or a traversal in a filled segment.
  expect(endpointFor("GET", "workspace/usage")).toBeNull();
  expect(endpointFor("GET", "workspace/radar")).toBeNull();
  expect(endpointFor("GET", "workspace/artifacts")).toBeNull();
  expect(endpointFor("GET", "workspace/memory")).toBeNull();
  expect(endpointFor("GET", "workspace/team")).toBeNull();
  expect(endpointFor("POST", "api/agents")).toBeNull();
  expect(endpointFor("GET", "objects")).toBeNull();
  expect(endpointFor("GET", "objects/../secrets")).toBeNull();
  expect(endpointFor("DELETE", "objects/scheduled_task/nightly")).toBeNull();
});

/** The fence is a column on the route table rather than a second list of kinds held here: the kinds
 *  a frame may reach are stated beside the rows that read them, and the shell and the page's own link
 *  handler ask the same question of it. */
test("navigation admits conversations, apps, new chats, and sections, and refuses the rest", () => {
  expect(framedNavigation("#/c/" + CONVERSATION_ID)).toBe(true);
  expect(framedNavigation("#/new/" + AGENT_ID)).toBe(true);
  expect(framedNavigation("#/agents/" + AGENT_ID)).toBe(true);
  expect(framedNavigation("#/connectors")).toBe(true);
  expect(framedNavigation("#/wiki?open=run%2Fabc")).toBe(true);
  expect(framedNavigation("#/workspace/connectors")).toBe(true);
  expect(framedNavigation("#/admin")).toBe(false);
  expect(framedNavigation("#/workspace/team")).toBe(false);
  expect(framedNavigation("#/agents/" + AGENT_ID + "/conversations/" + CONVERSATION_ID + "/slots/changes")).toBe(false);
  expect(framedNavigation("#/")).toBe(false);
  expect(framedNavigation("https://evil.example.com")).toBe(false);
});

test("ready is answered with the member's own init payload", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toEqual({
    ufo: "init",
    member: MEMBER,
    agents: AGENTS,
    agentId: AGENT_ID,
    place: {},
    portal: location.origin,
  });
});

/** The page holds a place the way a portal tab does, so every key of it crosses the frame. A
 *  boundary carrying one field would leave the page to guess the rest of the screen the member
 *  asked for. */
test("init carries the pane's whole place", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID, place: PLACE });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ ufo: "init", place: PLACE });
});

/** The page's own step of the trail crosses with it, so the band inside the frame names where the
 *  member is with the words the tab title uses. It rides `init` and no further: a place change is a
 *  new screen inside the page, not a new step above it. */
test("init carries the addressed step the page stands at, and a place change does not", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID, crumb: CRUMB });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({
    ufo: "init",
    crumb: { ...CRUMB, at: new URL(CRUMB.at, location.origin + BASE).href },
  });
  bridge.place(PLACE);
  await vi.waitFor(() => expect(posted).toHaveLength(2));
  expect(posted[1]).toEqual({ ufo: "place", place: PLACE });
});

test("a tabled GET is forwarded and its payload returned", async () => {
  const chats = { chats: [{ conversation_id: CONVERSATION_ID }] };
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(chats));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver({ ufo: "call", id: "r1", method: "GET", path: "api/chats" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(fetchMock.mock.calls[0][0]).toBe("/surface/web/api/chats");
  expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "GET" });
  expect(posted[0]).toEqual({
    ufo: "data",
    id: "r1",
    ok: true,
    status: 200,
    body: JSON.stringify(chats),
    refusal: null,
    fault: null,
  });
});

test("a call off the table is refused without a fetch", async () => {
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver({ ufo: "call", id: "r2", method: "GET", path: "workspace/usage" }, iframe.contentWindow);
  deliver({ ufo: "call", id: "r3", method: "PUT", path: "api/agents" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(2));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(posted[0]).toMatchObject({ ufo: "data", id: "r2", ok: false });
  expect(posted[1]).toMatchObject({ ufo: "data", id: "r3", ok: false });
});

test("a json row forwards its object body and returns the result", async () => {
  const result = { ok: true, name: "nightly", detail: "" };
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(result));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    {
      ufo: "call",
      id: "w1",
      method: "POST",
      path: "objects/scheduled_task",
      body: { name: "nightly", spec: { paused: true }, agent: AGENT_ID },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  const call = fetchMock.mock.calls[0];
  expect(call[0]).toBe("/surface/web/objects/scheduled_task");
  expect(call[1]).toMatchObject({ method: "POST" });
  expect(JSON.parse(String(call[1]?.body))).toEqual({
    name: "nightly",
    spec: { paused: true },
    agent: AGENT_ID,
  });
  expect(posted[0]).toEqual({
    ufo: "data",
    id: "w1",
    ok: true,
    status: 200,
    body: JSON.stringify(result),
    refusal: null,
    fault: null,
  });
});

test("a text row posts the member's message and returns the admission", async () => {
  const admitted = { turn_id: "t1", conversation_id: CONVERSATION_ID, opened_run: true };
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(admitted));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID, chatSurface: true });
  deliver(
    {
      ufo: "call",
      id: "c1",
      method: "POST",
      path: "agents/" + AGENT_ID + "/chat?conversation=" + CONVERSATION_ID,
      body: "hello",
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  const call = fetchMock.mock.calls[0];
  expect(call[0]).toBe("/surface/web/agents/" + AGENT_ID + "/chat?conversation=" + CONVERSATION_ID);
  expect(call[1]).toMatchObject({ method: "POST", body: "hello" });
  expect(posted[0]).toEqual({
    ufo: "data",
    id: "c1",
    ok: true,
    status: 200,
    body: JSON.stringify(admitted),
    refusal: null,
    fault: null,
  });
});

test("an intent rides through only when its verb is a page's own control", async () => {
  const applied = { applied: true, message: "Rebuilding." };
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(applied));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    {
      ufo: "call",
      id: "i1",
      method: "POST",
      path: "agents/" + AGENT_ID + "/intents",
      body: { verb: "rebuild_reports" },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(posted[0]).toMatchObject({ ufo: "data", id: "i1", ok: true });

  deliver(
    {
      ufo: "call",
      id: "i2",
      method: "POST",
      path: "agents/" + AGENT_ID + "/intents",
      body: { verb: "add_member", email: "x@example.com", admin: true },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(2));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(posted[1]).toMatchObject({ ufo: "data", id: "i2", ok: false });
});

test("a form call reassembles the multipart body the page decomposed", async () => {
  const admitted = { turn_id: "t1", conversation_id: CONVERSATION_ID, opened_run: true };
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(admitted));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID, chatSurface: true });
  deliver(
    {
      ufo: "call",
      id: "f1",
      method: "POST",
      path: "agents/" + AGENT_ID + "/chat?conversation=new",
      form: [
        { name: "message", value: "hello" },
        { name: "files", value: new File(["bytes"], "notes.txt", { type: "text/plain" }) },
      ],
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  const sent = fetchMock.mock.calls[0][1]?.body as FormData;
  expect(sent).toBeInstanceOf(FormData);
  expect(sent.get("message")).toBe("hello");
  expect((sent.get("files") as File).name).toBe("notes.txt");
  expect(posted[0]).toMatchObject({ ufo: "data", id: "f1", ok: true });
});

test("a chat post from a frame that is not the chat surface is refused without a fetch", async () => {
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    {
      ufo: "call",
      id: "v1",
      method: "POST",
      path: "agents/" + AGENT_ID + "/chat?conversation=new",
      body: "hello",
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(posted[0]).toMatchObject({ ufo: "data", id: "v1", ok: false });
});

test("a form on a row that takes none is refused without a fetch", async () => {
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    { ufo: "call", id: "f2", method: "GET", path: "api/chats", form: [] },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(posted[0]).toMatchObject({ ufo: "data", id: "f2", ok: false });
});

test("a body of the wrong shape for its row is refused without a fetch", async () => {
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    { ufo: "call", id: "b1", method: "POST", path: "objects/scheduled_task", body: "paused" },
    iframe.contentWindow,
  );
  deliver(
    { ufo: "call", id: "b2", method: "POST", path: "agents/" + AGENT_ID + "/chat", body: "  " },
    iframe.contentWindow,
  );
  deliver(
    { ufo: "call", id: "b3", method: "GET", path: "api/chats", body: { extra: true } },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(3));
  expect(fetchMock).not.toHaveBeenCalled();
  for (const reply of posted) expect(reply).toMatchObject({ ufo: "data", ok: false });
});

test("an accepted navigation target moves the shell; a refused one does not", () => {
  const { iframe } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver({ ufo: "navigate", to: "#/c/" + CONVERSATION_ID }, iframe.contentWindow);
  expect(location.hash).toBe("#/c/" + CONVERSATION_ID);
  deliver({ ufo: "navigate", to: "#/admin" }, iframe.contentWindow);
  expect(location.hash).toBe("#/c/" + CONVERSATION_ID);
});

/** A page's navigation is a navigation like any other, so the router writes it and the route every
 *  screen renders from names the conversation as the message lands. A bare hash write left that route
 *  to the browser's own `hashchange`, a task later, so the drawer over the page stayed open and the
 *  arrival never landed. A refused target moves neither the address nor the route. */
test("heldRoute names the conversation a frame's navigate message asked for", () => {
  const { iframe } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });

  deliver({ ufo: "navigate", to: "#/c/" + CONVERSATION_ID }, iframe.contentWindow);

  expect(heldRoute()).toEqual({ kind: "chat", conversationId: CONVERSATION_ID });

  deliver({ ufo: "navigate", to: "#/admin" }, iframe.contentWindow);

  expect(heldRoute()).toEqual({ kind: "chat", conversationId: CONVERSATION_ID });
});

test("a call with no usable id is dropped without a reply or a throw", async () => {
  const fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver({ ufo: "call", method: "GET", path: "api/agents" }, iframe.contentWindow);
  deliver({ ufo: "call", id: "", method: "GET", path: "api/agents" }, iframe.contentWindow);
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(posted).toHaveLength(0);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("a stream row relays its events as frames and closes with end", async () => {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const bytes = (text: string) => controller.enqueue(new TextEncoder().encode(text));
      bytes("event: delta\ndata: {\"text\":\"hel\"}\n\n");
      bytes("event: delta\ndata: {\"text\":\"lo\"}\n\nevent: terminal\ndata: {\"status\":\"done\"}\n\n");
      controller.close();
    },
  });
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(body));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    { ufo: "call", id: "s1", method: "GET", path: "turns/" + CONVERSATION_ID + "/stream" },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(5));
  expect(posted[0]).toEqual({ ufo: "opened", id: "s1" });
  expect(posted[1]).toEqual({ ufo: "frame", id: "s1", event: "delta", data: '{"text":"hel"}' });
  expect(posted[2]).toEqual({ ufo: "frame", id: "s1", event: "delta", data: '{"text":"lo"}' });
  expect(posted[3]).toEqual({ ufo: "frame", id: "s1", event: "terminal", data: '{"status":"done"}' });
  expect(posted[4]).toEqual({ ufo: "end", id: "s1" });
});

test("a place changing while the frame stands is posted, and a later ready carries it", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  bridge.place(PLACE);
  await vi.waitFor(() => expect(posted).toHaveLength(2));
  expect(posted[1]).toEqual({ ufo: "place", place: PLACE });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(3));
  expect(posted[2]).toMatchObject({ ufo: "init", place: PLACE });
});

test("a message from a child of the homepage window is ignored", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    { ufo: "ready" },
    { parent: iframe.contentWindow, postMessage: () => {} },
  );
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(posted).toHaveLength(0);
});
