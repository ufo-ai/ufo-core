import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { attachBridge } from "@/lib/bridge";
import { MainAgentProvider } from "@/lib/mainAgent";
import { chatHash, homeConversationLane, homeHash } from "@/lib/route";
import type { Agent } from "@/lib/types";
import { HomepageFrame } from "@/views/HomepageFrame";

import { AGENT, AGENT_ID, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, SECOND, SECOND_ID, useStreamFake, wire } from "./harness";

vi.mock("@/lib/bridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/bridge")>();
  return { ...actual, attachBridge: vi.fn(actual.attachBridge) };
});

const HOMEPAGE_URL = "/surface/sites/tok-abc/";

const SET = {
  state: "set",
  url: HOMEPAGE_URL,
  visibility: "workspace",
  updated_at: "2026-08-16T00:00:00Z",
};

const LISTED = {
  id: CONVO_ID,
  agent: null,
  surface: "web",
  surface_label: null,
  audience: "member:m1",
  member_email: MEMBER.email,
  description: "Pick one thread",
  source: null,
  speakers: [MEMBER.email],
  turn_count: 1,
  created_at: "2026-08-01T09:00:00",
  last_turn_at: "2026-08-01T09:00:01",
  readable: true,
  disclosable: false,
  commentable: false,
};

beforeEach(() => {
  location.hash = "";
  useStreamFake();
});

/** The main agent carrying its homepage the way the boot read does, for the pane's instant paint;
 *  the wire answers the live `/homepage` poll that keeps it current. `open` sets both to the same
 *  page unless a route overrides the poll — a redeploy drives the poll past what boot carried. */
function withHome(homepage: unknown): Agent {
  return { ...AGENT, homepage: homepage as Agent["homepage"] };
}

function open(routes: Parameters<typeof wire>[0], homepage?: unknown) {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(homepage ?? { state: "none" }),
    ...routes,
  });
  render(<App agents={[withHome(homepage)]} member={MEMBER} onAgents={() => {}} />);
}

/** A page pressed inside a lane keeps the member's track: the conversation it names opens in a lane
 *  of its own beside the page, rather than the transcript taking the whole screen. */
test("a page's conversation link opens a lane beside the app it was pressed in", async () => {
  location.hash = homeHash({ opens: [SECOND_ID] });
  wire({
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(SET),
    ...chatsOnWire([CHAT_ROW]),
  });
  render(
    <App
      agents={[AGENT, { ...SECOND, homepage: SET as Agent["homepage"] }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  const frame = (await screen.findByTitle("Second homepage")) as HTMLIFrameElement;
  fireEvent.load(frame);
  const asked = new MessageEvent("message", {
    data: { ufo: "navigate", to: chatHash(CONVO_ID) },
  });
  Object.defineProperty(asked, "source", { value: frame.contentWindow });
  fireEvent(window, asked);

  await waitFor(() =>
    expect(location.hash).toBe(
      homeHash({ opens: [SECOND_ID, homeConversationLane(CONVO_ID)] }),
    ),
  );
});

test("a set homepage frames the bound site beside the conversation", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({}, SET);

  const frame = await screen.findByTitle("Assistant homepage");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe(HOMEPAGE_URL);
  expect(frame.getAttribute("sandbox")).toBe(
    "allow-scripts allow-same-origin allow-forms allow-popups allow-modals allow-downloads allow-pointer-lock",
  );
  expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
  expect(frame.getAttribute("allow")).toBe("fullscreen");
  // What shows through a frame whose page is still arriving is the pane's own background, in the
  // portal's inherited color-scheme — never the system scheme or a browser's default white canvas.
  expect(frame.className).toContain("bg-surface");
  expect(frame.className).toContain("[color-scheme:inherit]");
  expect(frame.parentElement!.className).toContain("bg-surface");
  // The page heads itself, so the pane draws no band and no title of its own over it — only the
  // shell's two acts, floating in the page's gutter.
  expect(screen.queryByRole("heading", { name: "Assistant" })).toBeNull();
  expect(screen.getByRole("button", { name: "Menu for Assistant" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit Assistant" })).toBeTruthy();
});

test("focusing the tab keeps a live homepage frame", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({}, SET);

  const first = (await screen.findByTitle("Assistant homepage")) as HTMLIFrameElement;
  fireEvent.load(first);
  fireEvent.focus(window);

  expect(screen.getAllByTitle("Assistant homepage")).toEqual([first]);
});

test("focusing the tab refreshes an ended homepage session", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  const hasFocus = vi.spyOn(document, "hasFocus").mockReturnValue(false);
  open({}, SET);

  const first = (await screen.findByTitle("Assistant homepage")) as HTMLIFrameElement;
  fireEvent.load(first);
  const ended = new MessageEvent("message", { data: { ufo: "site-session-ended" } });
  Object.defineProperty(ended, "source", { value: first.contentWindow });
  fireEvent(window, ended);
  expect(screen.getAllByTitle("Assistant homepage")).toEqual([first]);

  fireEvent.focus(window);

  const frames = screen.getAllByTitle("Assistant homepage");
  expect(frames.length).toBe(2);
  expect(frames[0]).toBe(first);
  expect(frames[1].getAttribute("src")).toBe(HOMEPAGE_URL);
  expect(frames[1].className).toContain("opacity-0");
  hasFocus.mockRestore();
});

/** A redeploy remounts the page at the same URL, and the swap must never show the arriving copy's
 *  blank document: the standing frame holds the screen until the fresh one has loaded, the two
 *  cross-fade, and the replaced copy leaves once the fade is over. The bridge is the arriving
 *  frame's from the moment it mounts, so the page's own ready reaches the shell before the fade
 *  finishes. */
test("a redeploy keeps the standing page until the fresh frame loads, then swaps", async () => {
  let generation = 1;
  location.hash = "#/agents/" + AGENT_ID;
  vi.useFakeTimers();
  try {
    open(
      { "/homepage": () => json({ ...SET, deploy_generation: generation }) },
      { ...SET, deploy_generation: 1 },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
      await Promise.resolve();
    });
    const first = screen.getByTitle("Assistant homepage");
    expect(first.className).toContain("opacity-0");
    fireEvent.load(first);
    expect(first.className).toContain("opacity-100");

    generation = 2;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    const both = screen.getAllByTitle("Assistant homepage");
    expect(both.length).toBe(2);
    expect(both[0]).toBe(first);
    expect(first.className).toContain("opacity-100");
    expect(both[1].className).toContain("opacity-0");
    expect(both[1].className).toContain("pointer-events-none");

    const answered: unknown[] = [];
    vi.spyOn(both[1] as HTMLIFrameElement, "contentWindow", "get").mockReturnValue({
      postMessage: (message: unknown) => void answered.push(message),
    } as unknown as Window);
    fireEvent(
      window,
      new MessageEvent("message", {
        data: { ufo: "ready" },
        source: (both[1] as HTMLIFrameElement).contentWindow,
      }),
    );
    expect(answered.some((message) => (message as { ufo?: string }).ufo === "init")).toBe(true);

    fireEvent.load(both[1]);
    expect(both[1].className).toContain("opacity-100");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(document.contains(first)).toBe(false);
    expect(screen.getAllByTitle("Assistant homepage").length).toBe(1);
  } finally {
    vi.useRealTimers();
  }
});

/** Two redeploys racing: a load from the copy already being replaced promotes nothing, so the
 *  member never watches superseded bytes fade in over the page they had. */
test("a load from a frame already being replaced does not resurrect it", async () => {
  let generation = 1;
  location.hash = "#/agents/" + AGENT_ID;
  vi.useFakeTimers();
  try {
    open(
      { "/homepage": () => json({ ...SET, deploy_generation: generation }) },
      { ...SET, deploy_generation: 1 },
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
      await Promise.resolve();
    });
    const first = screen.getByTitle("Assistant homepage");
    fireEvent.load(first);

    generation = 2;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    const both = screen.getAllByTitle("Assistant homepage");
    expect(both.length).toBe(2);

    fireEvent.load(first);
    expect(screen.getAllByTitle("Assistant homepage").length).toBe(2);
    expect(first.className).toContain("opacity-100");
    expect(both[1].className).toContain("opacity-0");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500);
    });
    expect(document.contains(both[1])).toBe(true);
    expect(both[1].className).toContain("opacity-0");
  } finally {
    vi.useRealTimers();
  }
});

/** The conversation stands in a lane of the screen's own track, and a lane is headed by its own
 *  band: the name of what it holds and the way out of it. The conversation draws no band of its own
 *  there, or the name and the way out would each be stated twice. */
test("the conversation beside a page is headed by the lane it stands in", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({}, SET);

  await screen.findByTitle("Assistant homepage");
  await userEvent.click(screen.getByRole("button", { name: "Edit Assistant" }));

  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=new");
  const conversation = await screen.findByRole("region", { name: "Assistant" });
  expect(conversation.querySelector('[data-slot="header"]')).toBeNull();
});

/** The band carries the one act the member cannot reach from the page — starting another
 *  conversation with this app — and no menu to switch between them: the toggle opens the one there
 *  is, and the sidebar is the list of the rest. The act is the band's own, so the full-width chat's
 *  own New pill is not drawn beside it. */
test("the right-side chat's band starts another conversation, and carries no menu", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open(
    {
      ...chatsOnWire([CHAT_ROW]),
      "/conversations$": () => json({ conversations: [LISTED] }),
    },
    SET,
  );

  await screen.findByTitle("Assistant homepage");
  expect(screen.queryByRole("region", { name: "Conversations" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Edit Assistant" }));

  expect(await screen.findByRole("heading", { level: 2, name: "Pick one thread" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "New conversation with Assistant" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "New" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Conversations with Assistant" })).toBeNull();
});

/** The toggle states the act it will perform. Shut, it is the mark of a chat; open, it is the mark
 *  of the lane it would close. It carries no word in either state — the mark alone stands in the
 *  page's gutter, and the act is named to a screen reader. It stays one control across both, so
 *  the press that opened the lane keeps the focus that opened it.
 *
 *  The band's own act founds another conversation, and is spent where the lane already stands on
 *  one nobody has spoken in: a second empty conversation is not a thing the member can want. */
test("the chat toggle states the act it will perform, and the band's act is spent when it is new", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({}, SET);

  await screen.findByTitle("Assistant homepage");
  const shut = screen.getByRole("button", { name: "Edit Assistant" });
  expect(shut.textContent).toBe("");
  expect(shut.getAttribute("aria-pressed")).toBe("false");

  await userEvent.click(shut);

  const open_ = await screen.findByRole("button", { name: "Close edit of Assistant" });
  expect(open_.textContent).toBe("");
  expect(open_.getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("button", { name: "Edit Assistant" })).toBeNull();

  // The lane stands on a conversation nobody has spoken in, so there is nothing to start.
  const founds = screen.getByRole("button", { name: "New conversation with Assistant" });
  expect(founds.hasAttribute("disabled")).toBe(true);

  await userEvent.click(open_);
  expect(screen.getByRole("button", { name: "Edit Assistant" })).toBeTruthy();
});

/** The toggle opens the editing conversation: the newest the rail carries for this app, whatever
 *  order the app's own index answers in. */
test("the chat toggle opens the newest directive conversation", async () => {
  const newer = "66666666-6666-4666-8666-666666666666";
  location.hash = "#/agents/" + AGENT_ID;
  open(
    {
      ...chatsOnWire([
        CHAT_ROW,
        {
          ...CHAT_ROW,
          conversation_id: newer,
          title: "Bold titles",
          last_at: "2026-08-09T09:00:00.000Z",
        },
      ]),
      "/conversations$": () =>
        json({
          conversations: [LISTED, { ...LISTED, id: newer, description: "Bold titles" }],
        }),
    },
    SET,
  );

  await screen.findByTitle("Assistant homepage");
  await userEvent.click(screen.getByRole("button", { name: "Edit Assistant" }));

  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=" + newer);
});

/** An app nobody has directed yet has no editing conversation, so the toggle opens the composer —
 *  and the send that founds one makes it the editing chat from then on. */
test("the chat toggle opens the composer when no directive conversation exists", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({}, SET);

  await screen.findByTitle("Assistant homepage");
  await userEvent.click(screen.getByRole("button", { name: "Edit Assistant" }));

  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=new");
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
});

/** The state the reporter's app is in: it stands on its page, the rail carries the member's chat
 *  with it, and the app's own conversation index does not answer that chat. An app accumulates
 *  conversations no member opened — every page build, every portal intent — and the index answers
 *  one bounded page over all of them, so on an app holding many hundreds the member's own chat falls
 *  outside that page while the rail still carries it under its own bound. The rail is what says
 *  which conversations this half can open, so the half opens the row it carries. */
function openApp(routes: Parameters<typeof wire>[0] = {}) {
  const wired = wire({
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(SET),
    "/setup": () => json({ own_page: false, connectors: [], credentials: [], standing: [] }),
    ...chatsOnWire([CHAT_ROW]),
    "/conversations$": () => json({ conversations: [] }),
    ...routes,
  });
  render(
    <App
      agents={[{ ...withHome(SET), main: false, app: "code" }]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );
  return wired;
}

test("the chat toggle opens a rail chat the app's index does not answer", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  const { calls } = openApp();

  await screen.findByTitle("Assistant homepage");
  await userEvent.click(screen.getByRole("button", { name: "Edit Assistant" }));

  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=" + CONVO_ID);
  const latched = await screen.findByRole("button", { name: "Close edit of Assistant" });
  expect(latched.getAttribute("aria-pressed")).toBe("true");
  expect(await screen.findByRole("heading", { level: 2, name: "Pick one thread" })).toBeTruthy();
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  await waitFor(() =>
    expect(calls.some((url) => url.includes("/transcript?conversation=" + CONVO_ID))).toBe(true),
  );
});

test("a link to a rail chat the app's index does not answer opens it beside the page", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=" + CONVO_ID;
  const { calls } = openApp();

  const frame = (await screen.findByTitle("Assistant homepage")) as HTMLIFrameElement;
  expect(await screen.findByRole("heading", { level: 2, name: "Pick one thread" })).toBeTruthy();
  expect(await screen.findByLabelText("Ask UFO")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Close edit of Assistant" }).getAttribute("aria-pressed"),
  ).toBe("true");
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  await waitFor(() =>
    expect(calls.some((url) => url.includes("/transcript?conversation=" + CONVO_ID))).toBe(true),
  );

  // The lane holds the conversation, so the page is not handed an id it cannot stand on.
  const sent: unknown[] = [];
  vi.spyOn(frame, "contentWindow", "get").mockReturnValue({
    postMessage: (message: unknown) => void sent.push(message),
  } as unknown as Window);
  fireEvent(
    window,
    new MessageEvent("message", { data: { ufo: "ready" }, source: frame.contentWindow }),
  );
  const init = sent.find((message) => (message as { ufo?: string }).ufo === "init") as {
    place: { opens?: string[] };
    banded: boolean;
  };
  expect(init.place.opens).toBeUndefined();
  // The page is the screen here, so it heads itself.
  expect(init.banded).toBe(false);
});

/** A home lane's band already names the page and holds the way out, so the frame it holds is told
 *  in `init` to draw no band of its own — one header per lane. */
test("a home lane's frame is told it stands under the lane band", async () => {
  location.hash = homeHash({ opens: [AGENT_ID] });
  wire({ "/homepage": () => json(SET) });
  render(
    <App
      agents={[{ ...withHome(SET), main: false, app: "code" }, CHAT_APP]}
      member={MEMBER}
      onAgents={() => {}}
    />,
  );

  const frame = (await screen.findByTitle("Assistant homepage")) as HTMLIFrameElement;
  const sent: unknown[] = [];
  vi.spyOn(frame, "contentWindow", "get").mockReturnValue({
    postMessage: (message: unknown) => void sent.push(message),
  } as unknown as Window);
  fireEvent(
    window,
    new MessageEvent("message", { data: { ufo: "ready" }, source: frame.contentWindow }),
  );
  const init = sent.find((message) => (message as { ufo?: string }).ufo === "init") as {
    banded: boolean;
  };
  expect(init.banded).toBe(true);
});

/** The roster arrives as a new array on every re-read — the boot reload, a status poll — and home
 *  mounts a frame per lane. A bridge rebound on it would abort every relay stream each page holds
 *  open, all at once; so the bridge stays bound and answers the next `ready` with the roster as it
 *  now stands. */
test("a roster arriving as a new array leaves the frame's bridge bound", () => {
  const attached = vi.mocked(attachBridge);
  attached.mockClear();
  const draw = (agents: Agent[], member: typeof MEMBER) => (
    <MainAgentProvider agents={agents}>
      <HomepageFrame
        agent={AGENT}
        member={member}
        url={HOMEPAGE_URL}
        generation={1}
        place={{}}
        banded
        onFounded={() => {}}
      />
    </MainAgentProvider>
  );
  const view = render(draw([AGENT], MEMBER));
  expect(attached).toHaveBeenCalledTimes(1);

  const reread = [{ ...AGENT }, { ...AGENT, id: CONVO_ID, name: "metrics", main: false }];
  view.rerender(draw(reread, { ...MEMBER }));
  expect(attached).toHaveBeenCalledTimes(1);

  const frame = screen.getByTitle("Assistant homepage") as HTMLIFrameElement;
  const sent: unknown[] = [];
  vi.spyOn(frame, "contentWindow", "get").mockReturnValue({
    postMessage: (message: unknown) => void sent.push(message),
  } as unknown as Window);
  fireEvent(
    window,
    new MessageEvent("message", { data: { ufo: "ready" }, source: frame.contentWindow }),
  );
  const init = sent.find((message) => (message as { ufo?: string }).ufo === "init") as {
    agents: Agent[];
  };
  expect(init.agents).toBe(reread);
});

/** A rename lands while the frame stands — the member changing the name, the boot read correcting
 *  it. The page's own step of the trail is read when the page asks, so the next `init` names the app
 *  the way the shell now does, and the bridge whose rebind would abort the page's streams stays
 *  bound. */
test("a renamed app reaches the standing frame's next init", () => {
  const attached = vi.mocked(attachBridge);
  attached.mockClear();
  const draw = (agent: Agent) => (
    <MainAgentProvider agents={[agent]}>
      <HomepageFrame
        agent={agent}
        member={MEMBER}
        url={HOMEPAGE_URL}
        generation={1}
        place={{}}
        banded
        onFounded={() => {}}
      />
    </MainAgentProvider>
  );
  const view = render(draw(AGENT));
  view.rerender(draw({ ...AGENT, name: "weather watch" }));
  expect(attached).toHaveBeenCalledTimes(1);

  const frame = screen.getByTitle("Weather Watch homepage") as HTMLIFrameElement;
  const sent: unknown[] = [];
  vi.spyOn(frame, "contentWindow", "get").mockReturnValue({
    postMessage: (message: unknown) => void sent.push(message),
  } as unknown as Window);
  fireEvent(
    window,
    new MessageEvent("message", { data: { ufo: "ready" }, source: frame.contentWindow }),
  );
  const init = sent.find((message) => (message as { ufo?: string }).ufo === "init") as {
    crumb: { label: string };
  };
  expect(init.crumb.label).toBe("Weather Watch");
});

/** The chat app is the main agent, and its page is the conversation screen itself. So a chat the
 *  member opens is that page's own target: it stands in the page's column, and no lane over the
 *  page draws it a second time. */
test("a chat opened on the chat app stands in the page's column alone", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=" + CONVO_ID;
  const { calls } = wire({
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(SET),
    ...chatsOnWire([CHAT_ROW]),
    "/conversations$": () => json({ conversations: [LISTED], more: false }),
  });
  render(
    <App agents={[{ ...withHome(SET), app: "chat" }]} member={MEMBER} onAgents={() => {}} />,
  );

  const frame = (await screen.findByTitle("Assistant homepage")) as HTMLIFrameElement;
  expect(screen.queryByRole("region", { name: "Pick one thread" })).toBeNull();
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  // The page is the chat, so the act that opens a chat beside a page names nothing here.
  expect(screen.queryByRole("button", { name: "Edit Assistant" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Close edit of Assistant" })).toBeNull();
  expect(calls.some((url) => url.includes("/agents/" + AGENT_ID + "/conversations"))).toBe(false);

  const sent: unknown[] = [];
  vi.spyOn(frame, "contentWindow", "get").mockReturnValue({
    postMessage: (message: unknown) => void sent.push(message),
  } as unknown as Window);
  fireEvent(
    window,
    new MessageEvent("message", { data: { ufo: "ready" }, source: frame.contentWindow }),
  );
  const init = sent.find((message) => (message as { ufo?: string }).ufo === "init") as {
    place: { opens?: string[] };
  };
  expect(init.place.opens).toEqual([CONVO_ID]);
});

/** An app whose page has not been built yet has none, and the pane draws its conversation — never a
 *  build placeholder. When the first page registers, the poll lands it and the frame appears on its
 *  own, without the member reloading. */
test("an app whose first page arrives shows it without a reload", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  let answer: unknown = { state: "none" };
  vi.useFakeTimers();
  try {
    open({ "/homepage": () => json(answer) }, { state: "none" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
      await Promise.resolve();
    });
    // No page yet: the app's conversation stands, and there is no frame.
    expect(screen.getByRole("region", { name: "Assistant" })).toBeTruthy();
    expect(document.querySelector("iframe")).toBeNull();

    answer = SET;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    const frame = screen.getByTitle("Assistant homepage");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("src")).toBe(HOMEPAGE_URL);
  } finally {
    vi.useRealTimers();
  }
});

test("an app with no homepage draws one column and no second half", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  open({}, { state: "none" });

  expect(await screen.findByRole("region", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Assistant homepage" })).toBeNull();
  expect(document.querySelector("iframe")).toBeNull();
});

test("the bare agents hash shows the apps list, not one app's homepage", async () => {
  location.hash = "#/agents";
  open({});

  expect(await screen.findByRole("heading", { name: "Apps" })).toBeTruthy();
  expect(await screen.findByRole("cell", { name: "Assistant" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Assistant homepage" })).toBeNull();
  expect(location.hash).toBe("#/agents");
});

/** The chat app, carrying the page every deploy ships it and its own conversation on the rail. */
function openChatApp(hash: string, homepage: unknown = SET) {
  location.hash = hash;
  wire({
    ...chatsOnWire([{ ...CHAT_ROW, agent_id: CHAT_APP_ID, agent_name: "chat" }]),
    "/transcript": () => json({ messages: [] }),
    "/homepage": () => json(homepage),
    "/conversations$": () => json({ conversations: [LISTED], more: false }),
  });
  const main = { ...CHAT_APP, main: true, homepage } as Agent;
  render(<App agents={[main]} member={MEMBER} onAgents={() => {}} />);
}

/** The chat app's page lists the conversations it holds and opens them itself, so the shell puts no
 *  act of its own over it: neither the Chat toggle every other app wears nor a History act beside
 *  it. */
test("the chat app's page wears no shell act", async () => {
  openChatApp("#/agents/" + CHAT_APP_ID + "?open=" + CONVO_ID);
  await screen.findByTitle("Chat homepage");

  expect(screen.queryByRole("button", { name: "History for Chat" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit Chat" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Menu for Chat" })).toBeNull();
  // One column still: the list is the page's, never a panel the portal draws beside it.
  expect(screen.queryByRole("region", { name: CHAT_ROW.title })).toBeNull();
});

/** The page is what makes it the chat app. An install serving none falls back to the conversation
 *  column every other page-less app draws. */
test("a chat app with no page draws the conversation column", async () => {
  openChatApp("#/agents/" + CHAT_APP_ID, { state: "none" });

  expect(await screen.findByRole("region", { name: "Chat" })).toBeTruthy();
  expect(document.querySelector("iframe")).toBeNull();
  expect(screen.queryByRole("button", { name: "History for Chat" })).toBeNull();
});
