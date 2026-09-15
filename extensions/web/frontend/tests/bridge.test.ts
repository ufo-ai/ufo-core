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
import type { Crumb } from "@/lib/title";
import type { Agent, Member } from "@/lib/types";

const MEMBER = { email: "member@example.com", admin: true };
const AGENT_ID = "11111111-1111-1111-1111-111111111111";
const AGENTS: Agent[] = [
  { id: AGENT_ID, name: "Radar", model: "openai/gpt-5", main: false, icon: "radar" },
];
const CONVERSATION_ID = "22222222-2222-2222-2222-222222222222";
const PLACE = { q: "tokens", range: "90d", opens: [CONVERSATION_ID] };
const CRUMB = { label: "Radar", at: "#/agents/" + AGENT_ID };

/** The shell compares message sources by identity against `iframe.contentWindow`, and replies to the source. */
function fakeFrame(): { iframe: HTMLIFrameElement; posted: unknown[] } {
  const posted: unknown[] = [];
  const contentWindow = { postMessage: (message: unknown) => void posted.push(message) };
  const iframe = { contentWindow, style: {} } as unknown as HTMLIFrameElement;
  return { iframe, posted };
}

function deliver(data: unknown, source: unknown): void {
  const event = new MessageEvent("message", { data });
  Object.defineProperty(event, "source", { value: source });
  window.dispatchEvent(event);
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body));
}

let bridge: BridgeHandle | null = null;

function attachBridge({
  member,
  crumb,
  ...config
}: Omit<BridgeConfig, "standing" | "banded"> & { member: Member; crumb?: Crumb }): BridgeHandle {
  return attach({
    ...config,
    banded: false,
    standing: () => ({ member, agents: AGENTS, crumb }),
  });
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
  expect(endpointFor("GET", "/agents/" + AGENT_ID + "/transcript")).toBeNull();
  expect(
    endpointFor(
      "GET",
      "/agents/" +
        AGENT_ID +
        "/conversations/" +
        CONVERSATION_ID +
        "/transcript?cursor=Mjo",
    ),
  ).toBeTruthy();
  expect(endpointFor("POST", "objects/scheduled_task")).toBeTruthy();
  expect(endpointFor("POST", "credentials")).toBeTruthy();
  expect(endpointFor("GET", "actions/report")).toBeTruthy();
  expect(endpointFor("GET", "actions/surface/slack")).toBeTruthy();
  expect(endpointFor("POST", "agents/" + AGENT_ID + "/actions/report/rebuild_report_digest")).toBeTruthy();
  expect(
    endpointFor("POST", "agents/" + AGENT_ID + "/actions/surface/slack/slack_connect"),
  ).toBeTruthy();
  expect(endpointFor("POST", "agents/" + AGENT_ID + "/chat?conversation=new")).toBeTruthy();
  expect(endpointFor("GET", "api/chats?conversation=" + AGENT_ID)).toBeTruthy();
  expect(endpointFor("POST", "preview")).toBeTruthy();
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

test("navigation admits conversations, apps, new chats, and sections, and refuses the rest", () => {
  expect(framedNavigation("#/c/" + CONVERSATION_ID)).toBe(true);
  expect(framedNavigation("#/new/" + AGENT_ID)).toBe(true);
  expect(framedNavigation("#/agents/" + AGENT_ID)).toBe(true);
  expect(framedNavigation("#/agents/" + AGENT_ID + "/setup")).toBe(true);
  expect(framedNavigation("#/connectors")).toBe(true);
  expect(framedNavigation("#/wiki?open=run%2Fabc")).toBe(true);
  expect(framedNavigation("#/workspace/artifacts")).toBe(true);
  expect(framedNavigation("#/workspace/connectors")).toBe(false);
  expect(framedNavigation("#/workspace/team")).toBe(false);
  expect(framedNavigation("#/workspace/team")).toBe(false);
  expect(framedNavigation("#/workspace/sources")).toBe(false);
  expect(framedNavigation("#/workspace/__proto__")).toBe(false);
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
    banded: false,
    place: {},
    portal: location.origin,
  });
});

test("init says whether the frame stands under a lane band", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attach({
    iframe,
    agentId: AGENT_ID,
    banded: true,
    standing: () => ({ member: MEMBER, agents: AGENTS }),
  });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ ufo: "init", banded: true });
});

test("ready is answered with the roster and crumb as they stand, not as they were at attach", async () => {
  const { iframe, posted } = fakeFrame();
  let agents = AGENTS;
  let crumb = CRUMB;
  bridge = attach({
    iframe,
    agentId: AGENT_ID,
    banded: false,
    standing: () => ({ member: MEMBER, agents, crumb }),
  });
  agents = [...AGENTS, { ...AGENTS[0], id: CONVERSATION_ID, name: "Metrics" }];
  crumb = { ...CRUMB, label: "Weather" };
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect((posted[0] as { agents: Agent[] }).agents.map((agent) => agent.name)).toEqual([
    "Radar",
    "Metrics",
  ]);
  expect((posted[0] as { crumb: Crumb }).crumb.label).toBe("Weather");
});

test("init carries the pane's whole place", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID, place: PLACE });
  deliver({ ufo: "ready" }, iframe.contentWindow);
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ ufo: "init", place: PLACE });
});

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

test("the framed ingress page reports an ended site session", () => {
  const ended = vi.fn();
  const { iframe } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID, onSessionEnded: ended });

  deliver({ ufo: "site-session-ended" }, {});
  expect(ended).not.toHaveBeenCalled();
  deliver({ ufo: "site-session-ended" }, iframe.contentWindow);
  expect(ended).toHaveBeenCalledOnce();
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

test("an action from a page is marked for the server's presentation gate", async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ applied: true, message: "" }));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  const other = "9f1d3c2b-0000-4000-8000-0000000000aa";
  deliver(
    {
      ufo: "call",
      id: "a1",
      method: "POST",
      path: "agents/" + other + "/actions/report/rebuild_report_digest",
      body: {},
      headers: { "x-ufo-frame": "0", authorization: "Bearer stolen" },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ ufo: "data", id: "a1", ok: true });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe(BASE + "/agents/" + other + "/actions/report/rebuild_report_digest");
  expect(init?.method).toBe("POST");
  expect(init?.body).toBe("{}");
  expect(init?.headers).toMatchObject({ "x-ufo-frame": "1", "content-type": "application/json" });
  expect(init?.headers).not.toHaveProperty("authorization");

});

test("the setup band's act rides through, and the fence names why the others do not", async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ applied: true, message: "" }));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    {
      ufo: "call",
      id: "c1",
      method: "POST",
      path: "agents/" + AGENT_ID + "/intents",
      body: { verb: "connect", kind: "connection", name: "github" },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(posted[0]).toMatchObject({ ufo: "data", id: "c1", ok: true });

  for (const [id, body] of [
    ["c2", { verb: "request", kind: "credential", name: "acme_api_key" }],
    ["c3", { verb: "grant_web_access", email: "x@example.com" }],
  ] as const) {
    deliver(
      { ufo: "call", id, method: "POST", path: "agents/" + AGENT_ID + "/intents", body },
      iframe.contentWindow,
    );
  }
  await vi.waitFor(() => expect(posted).toHaveLength(3));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  for (const refused of posted.slice(1)) {
    expect(refused).toMatchObject({
      ufo: "data",
      ok: false,
      error: "This intent is not available from an app page.",
    });
  }
});

test("an app page reads its own records, and no endpoint it was not given", async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ objects: [] }));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    { ufo: "call", id: "s1", method: "GET", path: "objects/scheduled_task" },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0]).toMatchObject({ ufo: "data", id: "s1", ok: true, status: 200 });

  for (const [id, path] of [
    ["s2", "agents/" + AGENT_ID + "/setup"],
    ["s3", "agents/" + AGENT_ID + "/spend"],
  ] as const) {
    deliver({ ufo: "call", id, method: "GET", path }, iframe.contentWindow);
  }
  await vi.waitFor(() => expect(posted).toHaveLength(3));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  for (const refused of posted.slice(1)) {
    expect(refused).toMatchObject({ ok: false, error: "This endpoint is not available." });
  }
});

test("a page's intent acts on its own app, and never widens an account", async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse({ applied: true }));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });

  const other = "9f1d3c2b-0000-4000-8000-0000000000aa";
  deliver(
    {
      ufo: "call",
      id: "c1",
      method: "POST",
      path: "/agents/" + other + "/intents",
      body: { verb: "connect", kind: "connection", name: "github" },
    },
    iframe.contentWindow,
  );
  deliver(
    {
      ufo: "call",
      id: "c2",
      method: "POST",
      path: "agents/" + AGENT_ID + "/intents",
      body: { verb: "connect", kind: "connection", name: "github", spec: { shared: true } },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(2));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(posted[0]).toMatchObject({ ok: false, error: "An app page connects an account to its own app." });
  expect(posted[1]).toMatchObject({
    ok: false,
    error: "An app page connects an account to itself alone.",
  });

  deliver(
    {
      ufo: "call",
      id: "c3",
      method: "POST",
      path: "agents/" + AGENT_ID + "/intents",
      body: { verb: "connect", kind: "connection", name: "github", spec: { shared: false } },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(3));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(posted[2]).toMatchObject({ ufo: "data", id: "c3", ok: true });

  deliver(
    {
      ufo: "call",
      id: "c4",
      method: "POST",
      path: "/agents/" + other + "/intents",
      body: { verb: "apply", kind: "scheduled_task", name: "daily-brief", spec: { paused: true } },
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(4));
  expect(posted[3]).toMatchObject({ ufo: "data", id: "c4", ok: true });
  expect(fetchMock).toHaveBeenCalledTimes(2);
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

test("a page renders an opened document's pages through the preview row", async () => {
  const rendered = { start_page: 1, page_count: 3, pages: ["cGFnZQ=="] };
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(rendered));
  vi.stubGlobal("fetch", fetchMock);
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  deliver(
    {
      ufo: "call",
      id: "p1",
      method: "POST",
      path: "preview",
      form: [
        { name: "file", value: new File(["%PDF-1.7"], "report.pdf", { type: "application/pdf" }) },
        { name: "start_page", value: "1" },
      ],
    },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(1));
  const sent = fetchMock.mock.calls[0][1]?.body as FormData;
  expect(fetchMock.mock.calls[0][0]).toBe(BASE + "/preview");
  expect((sent.get("file") as File).name).toBe("report.pdf");
  expect(sent.get("start_page")).toBe("1");
  expect(posted[0]).toMatchObject({ ufo: "data", id: "p1", ok: true });
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
  deliver({ ufo: "navigate", to: "#/workspace/team" }, iframe.contentWindow);
  expect(location.hash).toBe("#/c/" + CONVERSATION_ID);
});

test("heldRoute names the conversation a frame's navigate message asked for", () => {
  const { iframe } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });

  deliver({ ufo: "navigate", to: "#/c/" + CONVERSATION_ID }, iframe.contentWindow);

  expect(heldRoute()).toEqual({ kind: "chat", conversationId: CONVERSATION_ID });

  deliver({ ufo: "navigate", to: "#/workspace/team" }, iframe.contentWindow);

  expect(heldRoute()).toEqual({ kind: "chat", conversationId: CONVERSATION_ID });
});

test("a lane takes a frame's conversation link instead of the shell moving to it", () => {
  const { iframe } = fakeFrame();
  const laned: string[] = [];
  location.hash = "";
  bridge = attachBridge({
    iframe,
    member: MEMBER,
    agentId: AGENT_ID,
    onConversation: (conversationId) => void laned.push(conversationId),
  });

  deliver({ ufo: "navigate", to: "#/c/" + CONVERSATION_ID }, iframe.contentWindow);

  expect(laned).toEqual([CONVERSATION_ID]);
  expect(location.hash).toBe("");

  deliver({ ufo: "navigate", to: "#/agents/" + AGENT_ID }, iframe.contentWindow);

  expect(laned).toEqual([CONVERSATION_ID]);
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
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

/** A browser aborting a fetch errors the body stream, so the reader the shell holds rejects mid-relay. */
function abortingStream(signal: AbortSignal | null | undefined): ReadableStream<Uint8Array> {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: delta\ndata: {"text":"hi"}\n\n'));
      signal?.addEventListener("abort", () =>
        controller.error(new DOMException("signal is aborted without reason", "AbortError")),
      );
    },
  });
}

async function relayingStream(posted: unknown[], iframe: HTMLIFrameElement): Promise<void> {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (_url, init) => new Response(abortingStream(init?.signal))),
  );
  deliver(
    { ufo: "call", id: "s1", method: "GET", path: "turns/" + CONVERSATION_ID + "/stream" },
    iframe.contentWindow,
  );
  await vi.waitFor(() => expect(posted).toHaveLength(2));
}

test("a stream the page asked to close ends without stating a fault to the page", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  await relayingStream(posted, iframe);

  deliver({ ufo: "close", id: "s1" }, iframe.contentWindow);
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(posted).toHaveLength(2);
});

test("a stream the shell dropped on detach states no fault to the frame it let go of", async () => {
  const { iframe, posted } = fakeFrame();
  bridge = attachBridge({ iframe, member: MEMBER, agentId: AGENT_ID });
  await relayingStream(posted, iframe);

  bridge.detach();
  bridge = null;
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(posted).toHaveLength(2);
});
