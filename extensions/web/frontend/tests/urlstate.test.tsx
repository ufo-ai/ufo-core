import { act, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import type { Placement } from "@/kernel/pager";
import { usePlaceRecorder } from "@/kernel/place";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";
import {
  agentHash,
  artifactTarget,
  bootRoute,
  chatHash,
  newChatHash,
  parseHash,
  sectionHash,
  workspaceHash,
  type PlaceStep,
  type WorkspacePlace,
} from "@/lib/route";

import {
  goTo,
  json,
  useStreamFake,
  viewCard,
  wire,
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
} from "./harness";

const OLDER = {
  id: "a1",
  filename: "notes.txt",
  subject: "notes",
  media_type: "text/plain",
  size_bytes: 12,
  created_at: "2026-07-30T09:00:00",
  url: "/dl/notes.txt",
};

const NEWER = { ...OLDER, id: "a2", filename: "report.txt", created_at: "2026-07-31T09:00:00" };

const OLDER_KEY = OLDER.id;
const NEWER_KEY = NEWER.id;

/** A filename is whatever the agent sharing the file called it, and nothing bounds it: the column
 *  is free text and the share tool takes the name it is given. A lane is named by the file's own
 *  id, so a file named past what a lane id may hold stands in the track like any other file. */
const LONG = {
  ...OLDER,
  id: "a3",
  filename: "quarterly-" + "reconciliation-and-variance-".repeat(20) + "notes.txt",
};

const MIXED_CASE_CONVO_ID = "8C0ACA03-8d29-4959-af47-8ae69bed7e48";

beforeEach(() => {
  useStreamFake();
});

function serve() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("/objects/site")) return json({ objects: [] });
      if (url.includes("/workspace/artifacts")) {
        const paged = url.includes("after=");
        return json({
          artifacts: paged ? [OLDER] : [NEWER],
          older: paged ? null : "c-older",
          newer: paged ? "c-newer" : null,
        });
      }
      if (url.includes("/workspace/memory")) {
        return json({ available: true, kinds: ["fact", "profile"], matches: [] });
      }
      if (url.includes("/workspace/sources")) {
        return json({
          sources: [
            {
              name: "notion-main",
              backend: "notion",
              stream: "pages",
              account_id: "acct",
              base_url: null,
              owner_email: "member@example.com",
              shared: false,
              own: true,
              consecutive_errors: 0,
              next_sync_at: "2026-08-01T06:00:00",
            },
            {
              name: null,
              backend: "rss",
              stream: "feed",
              account_id: null,
              base_url: "https://example.com/feed",
              owner_email: null,
              shared: true,
              own: true,
              consecutive_errors: 0,
              next_sync_at: "2026-08-02T09:30:00",
            },
          ],
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return new Response("file body");
    }),
  );
  return calls;
}

test("a workspace tab and a section carry the same place and parse back to it", () => {
  const place = {
    kind: "fact",
    after: "c2",
    q: "roadmap",
    chip: "Workspace",
    face: "table",
    opens: [OLDER_KEY],
  };
  expect(parseHash(workspaceHash("sources", place))).toEqual({
    kind: "workspace",
    view: "sources",
    place,
  });
  expect(parseHash(sectionHash("artifacts", place))).toEqual({
    kind: "section",
    section: "artifacts",
    place,
  });
  expect(parseHash("#/workspace/artifacts")).toEqual({ kind: "home" });
  expect(parseHash("#/sites")).toEqual({ kind: "home" });
  expect(parseHash("#/memory")).toEqual({ kind: "home" });
});

test("the memory hash carries its place and parses back to it", () => {
  const place = { kind: "fact", q: "roadmap" };
  expect(workspaceHash("memory", place)).toBe("#/workspace/memory?kind=fact&q=roadmap");
  expect(parseHash(workspaceHash("memory", place))).toEqual({
    kind: "workspace",
    view: "memory",
    place,
  });
  expect(parseHash(workspaceHash("memory"))).toEqual({
    kind: "workspace",
    view: "memory",
    place: {},
  });
});

test("conversation slots parse in chat and standalone URLs", () => {
  expect(parseHash(chatHash(CONVO_ID, "changes"))).toEqual({
    kind: "chat",
    conversationId: CONVO_ID,
    slot: "changes",
  });
  expect(
    parseHash("#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/changes"),
  ).toEqual({
    kind: "conversation-slot",
    agentId: AGENT.id,
    conversationId: CONVO_ID,
    slot: "changes",
  });
  const root = "99999999-9999-4999-8999-999999999999";
  expect(
    parseHash(
      "#/agents/" +
        AGENT.id +
        "/conversations/" +
        CONVO_ID +
        "/slots/changes?root=" +
        root,
    ),
  ).toEqual({
    kind: "conversation-slot",
    agentId: AGENT.id,
    conversationId: CONVO_ID,
    slot: "changes",
    rootConversationId: root,
  });
});

test("an app is one address, and the conversation it has open is a place on it", () => {
  expect(agentHash(AGENT.id)).toBe("#/agents/" + AGENT.id);
  expect(parseHash("#/agents/" + AGENT.id)).toEqual({
    kind: "agent",
    agentId: AGENT.id,
    place: {},
  });
  expect(agentHash(AGENT.id, { opens: [CONVO_ID] })).toBe(
    "#/agents/" + AGENT.id + "?open=" + CONVO_ID,
  );
  expect(parseHash("#/agents/" + AGENT.id + "?open=" + CONVO_ID)).toEqual({
    kind: "agent",
    agentId: AGENT.id,
    place: { opens: [CONVO_ID] },
  });
});

test("the address carries every slot standing on the screen, in track order", () => {
  const none: WorkspacePlace = { after: "c-older" };
  expect(sectionHash("artifacts", none)).toBe("#/artifacts?after=c-older");
  expect(parseHash(sectionHash("artifacts", none))).toEqual({
    kind: "section",
    section: "artifacts",
    place: none,
  });

  const one: WorkspacePlace = { opens: [OLDER_KEY] };
  expect(parseHash(sectionHash("artifacts", one))).toEqual({
    kind: "section",
    section: "artifacts",
    place: one,
  });

  const many: WorkspacePlace = { opens: [CONVO_ID, AGENT.id, OLDER_KEY] };
  expect(sectionHash("artifacts", many)).toBe(
    "#/artifacts?open=" + CONVO_ID + "%7E" + AGENT.id + "%7E" + OLDER_KEY,
  );
  expect(parseHash(sectionHash("artifacts", many))).toEqual({
    kind: "section",
    section: "artifacts",
    place: many,
  });
});

test("a link naming one thing to open stands on the screen as a track of one", () => {
  expect(parseHash("#/artifacts?open=" + OLDER_KEY)).toEqual({
    kind: "section",
    section: "artifacts",
    place: { opens: [OLDER_KEY] },
  });
  expect(parseHash("#/agents/" + AGENT.id + "?open=" + CONVO_ID + "&q=roadmap")).toEqual({
    kind: "agent",
    agentId: AGENT.id,
    place: { q: "roadmap", opens: [CONVO_ID] },
  });
});

test("a mangled track names no route, and never a screen quietly missing its slots", () => {
  expect(parseHash("#/wiki?open=a~~b")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/wiki?open=~")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/wiki?open=~" + CONVO_ID)).toEqual({ kind: "bad-link" });
  expect(parseHash("#/artifacts?open=" + CONVO_ID + "~")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/workspace/team?open=" + CONVO_ID + "~~" + AGENT.id)).toEqual({
    kind: "bad-link",
  });
  expect(parseHash("#/agents/" + AGENT.id + "?q=roadmap&open=a~~b")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/wiki?open=a%0Ab")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/wiki?open=" + "x".repeat(300))).toEqual({ kind: "bad-link" });
});

/** A track is the row of lanes a screen stands on, so one id in it twice is two hosts over one
 *  record and a row longer than the store holds is lanes the member would lose on the way back.
 *  Neither is a track, and the address says so rather than standing a screen on it. */
test("a track naming a slot twice, or more lanes than a screen holds, names no route", () => {
  const lanes = (count: number) =>
    Array.from({ length: count }, (_, at) => "object/report/" + at).join("~");

  expect(parseHash("#/artifacts?open=a~b~a")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/agents/" + AGENT.id + "?open=" + CONVO_ID + "~" + CONVO_ID)).toEqual({
    kind: "bad-link",
  });
  expect(parseHash("#/wiki?open=" + lanes(25))).toEqual({ kind: "bad-link" });
  expect(parseHash("#/wiki?open=" + lanes(24))).toEqual({
    kind: "section",
    section: "wiki",
    place: { opens: lanes(24).split("~") },
  });
});

test("a track no address can carry raises where it was made up", () => {
  expect(() => sectionHash("artifacts", { opens: [CONVO_ID + "~" + AGENT.id] })).toThrow(/slot id/);
  expect(() => sectionHash("artifacts", { opens: [""] })).toThrow(/slot id/);
  expect(() => agentHash(AGENT.id, { opens: [CONVO_ID, "a~b"] })).toThrow(/slot id/);
  expect(() => sectionHash("artifacts", { opens: ["x".repeat(300)] })).toThrow(/slot id/);
  expect(() => sectionHash("artifacts", { opens: [CONVO_ID, CONVO_ID] })).toThrow(/twice/);
  expect(() =>
    sectionHash("artifacts", {
      opens: Array.from({ length: TRACK_MAX_SLOTS + 1 }, (_, at) => "object/report/" + at),
    }),
  ).toThrow(/at most/);
});

function stepping(place: WorkspacePlace) {
  const steps: PlaceStep[] = [];
  const places: WorkspacePlace[] = [];
  const held = renderHook(
    ({ place }: { place: WorkspacePlace }) =>
      usePlaceRecorder({
        view: "artifacts",
        place,
        remountOnPlace: false,
        onPlace: (next, step) => {
          steps.push(step);
          places.push(next);
          held.rerender({ place: next });
        },
      }),
    { initialProps: { place } },
  );
  return {
    steps,
    places,
    record: (patch: Placement) => act(() => held.result.current.record(patch)),
  };
}

test("opening a slot pushes, and closing the newest unwinds the entry that opened it", () => {
  const { steps, places, record } = stepping({});

  record({ opens: [CONVO_ID] });
  record({ opens: [CONVO_ID, AGENT.id] });
  record({ opens: [CONVO_ID] });

  expect(steps).toEqual(["push", "push", "back"]);
  expect(places.map((place) => place.opens)).toEqual([
    [CONVO_ID],
    [CONVO_ID, AGENT.id],
    [CONVO_ID],
  ]);
});

test("closing a slot that is not the newest keeps the entry above it standing", () => {
  const { steps, record } = stepping({});

  record({ opens: [CONVO_ID] });
  record({ opens: [CONVO_ID, AGENT.id] });
  record({ opens: [AGENT.id] });
  record({ opens: [] });

  expect(steps).toEqual(["push", "push", "replace", "replace"]);
});

test("a track a link arrived with closes by replacement, since this screen pushed nothing", () => {
  const { steps, record } = stepping({ opens: [CONVO_ID, AGENT.id] });

  record({ opens: [CONVO_ID] });
  record({ opens: [] });

  expect(steps).toEqual(["replace", "replace"]);
});

test("paging with slots standing pushes and leaves nothing to unwind", () => {
  const { steps, record } = stepping({});

  record({ opens: [CONVO_ID] });
  record({ after: "c-older", opens: [] });
  record({ opens: [AGENT.id] });
  record({ opens: [] });

  expect(steps).toEqual(["push", "push", "push", "back"]);
});

test("a hash under the torn-out subagent namespace names no route", () => {
  expect(parseHash("#/subagents/deep_research")).toEqual({ kind: "home" });
  expect(parseHash("#/subagents/deep_research/conversations/" + CONVO_ID)).toEqual({
    kind: "home",
  });
});

test("a conversation named as a query target boots on that conversation", () => {
  expect(bootRoute("", "?c=" + CONVO_ID)).toEqual({ kind: "chat", conversationId: CONVO_ID });
  expect(bootRoute("#/agents", "?c=" + CONVO_ID)).toEqual({ kind: "agents" });
  expect(bootRoute("", "")).toEqual({ kind: "home" });
});

test("the sign-in that founded the workspace boots on its opening chat", () => {
  expect(bootRoute("", "?first=1")).toEqual({ kind: "first-run" });
  expect(parseHash("#/first-run")).toEqual({ kind: "first-run" });
  expect(bootRoute("#/first-run", "")).toEqual({ kind: "first-run" });
  expect(bootRoute("", "?first=1&c=" + CONVO_ID)).toEqual({
    kind: "chat",
    conversationId: CONVO_ID,
  });
  expect(bootRoute("#/agents", "?first=1")).toEqual({ kind: "agents" });
  expect(bootRoute("", "")).toEqual({ kind: "home" });
});

test("a conversation permalink that is not a lowercase uuid names no route", () => {
  expect(parseHash(chatHash(MIXED_CASE_CONVO_ID))).toEqual({ kind: "bad-link" });
  expect(parseHash("#/c/not-a-conversation")).toEqual({ kind: "bad-link" });
  expect(bootRoute("", "?c=" + MIXED_CASE_CONVO_ID)).toEqual({ kind: "bad-link" });
});

test("the conversation a sign-in carried through opens, and the hash names it", async () => {
  history.replaceState(null, "", location.pathname + "?c=" + CONVO_ID);
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => json({ messages: [] }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(location.hash).toBe(chatHash(CONVO_ID));
});

/** One line answers every address the portal cannot read — a mis-cased conversation permalink and a
 *  mangled slot track alike — so it names no kind of link. */
test("an address the portal cannot read reports a bad link, whichever part is mangled", async () => {
  history.replaceState(null, "", chatHash(MIXED_CASE_CONVO_ID));
  wire({ "/transcript": () => json({ messages: [] }) });
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  expect(screen.queryByLabelText("Message the app")).toBeNull();
  view.unmount();

  history.replaceState(null, "", "#/wiki?open=a~~b");
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
});

test("a reload lands on the page and the open artifact the hash names", async () => {
  location.hash = sectionHash("artifacts", { after: "c-older", opens: [OLDER_KEY] });
  const calls = serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("file body")).toBeTruthy();
  expect(calls.some((url) => url.includes("after=c-older"))).toBe(true);
  expect(screen.getByRole("button", { name: /^Close/ })).toBeTruthy();
});

test("paging writes the cursor to the hash and Back steps to the previous page", async () => {
  location.hash = sectionHash("artifacts");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Older" }));

  expect(await viewCard("notes.txt")).toBeTruthy();
  expect(location.hash).toContain("after=c-older");

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/artifacts"));
  expect(await viewCard("report.txt")).toBeTruthy();
});

test("opening an artifact names it in the hash and closing clears it", async () => {
  location.hash = sectionHash("artifacts", { after: "c-older" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toContain("open=");

  await userEvent.click(screen.getByRole("button", { name: /^Close/ }));
  await waitFor(() => expect(location.hash).not.toContain("open="));
  expect(location.hash).toContain("after=c-older");
  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());
});

const ARTIFACTS = sectionHash("artifacts", { opens: [NEWER_KEY] });

function serveLong() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return json({ objects: [] });
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [LONG], older: null });
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return new Response("file body");
    }),
  );
}

test("a file named past what a lane id holds opens, closes, and comes back off the address", async () => {
  location.hash = sectionHash("artifacts");
  serveLong();
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await viewCard(LONG.filename));

  expect(await screen.findByText("file body")).toBeTruthy();
  const held = location.hash;
  expect(held).toBe(sectionHash("artifacts", { opens: [LONG.id] }));

  await userEvent.click(screen.getByRole("button", { name: /^Close/ }));
  await waitFor(() => expect(location.hash).toBe(sectionHash("artifacts")));
  expect(screen.queryByText("file body")).toBeNull();
  first.unmount();

  history.replaceState(null, "", held);
  serveLong();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toBe(held);
});

test("a screen left holding a track is holding the same one on the way back", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("report.txt"));
  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await waitFor(() => expect(location.hash).toBe(newChatHash(AGENT.id)));
  expect(screen.queryByText("file body")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));

  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));
  expect(await screen.findByText("file body")).toBeTruthy();
});

test("a reload of a bare screen address lands on the track that screen was left holding", async () => {
  serve();
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("report.txt"));
  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));
  first.unmount();

  history.replaceState(null, "", sectionHash("artifacts"));
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));
  expect(await screen.findByText("file body")).toBeTruthy();
});

test("coming back to a screen replaces the address rather than pushing onto it", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("report.txt"));
  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await waitFor(() => expect(location.hash).toBe(newChatHash(AGENT.id)));
  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));

  history.back();

  await waitFor(() => expect(location.hash).toBe(newChatHash(AGENT.id)));
});

test("a link stating its own track overrules the one the screen was left holding", async () => {
  serve();
  const first = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("report.txt"));
  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));
  first.unmount();

  const link = sectionHash("artifacts", { after: "c-older", opens: [OLDER_KEY] });
  history.replaceState(null, "", link);
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toBe(link);

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));

  await waitFor(() =>
    expect(location.hash).toBe(sectionHash("artifacts", { opens: [OLDER_KEY] })),
  );
});

test("a slot the member shut is not handed back by the screen's own store", async () => {
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  await userEvent.click(await viewCard("report.txt"));
  await waitFor(() => expect(location.hash).toBe(ARTIFACTS));

  await userEvent.click(screen.getByRole("button", { name: /^Close/ }));
  await waitFor(() => expect(location.hash).toBe("#/artifacts"));

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));

  await waitFor(() => expect(location.hash).toBe("#/artifacts"));
  expect(screen.queryByText("file body")).toBeNull();
});

test("search and chip ride the hash by replacement, never as history entries", async () => {
  location.hash = "#/agents";
  location.hash = workspaceHash("sources");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("tab", { name: "Workspace" }));
  await userEvent.type(screen.getByRole("searchbox"), "rss{enter}");


  await waitFor(() => expect(location.hash).toContain("q=rss"));
  expect(location.hash).toContain("chip=Workspace");

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
});

/** One shell heads every tab and every section from one box, so the box has to belong to the view
 *  under it. A term typed and not submitted, left standing across a tab press, is a term the next
 *  Enter narrows the wrong records by. */
test("a search term typed and not submitted does not follow the member to the next tab", async () => {
  location.hash = workspaceHash("team");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await goTo("Sources");
  await userEvent.type(await screen.findByLabelText("Search sources"), "rss");
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("rss");
  expect(location.hash).not.toContain("q=");

  await goTo("Credentials");

  const box = (await screen.findByLabelText("Search credentials")) as HTMLInputElement;
  expect(box.value).toBe("");
});

test("a reloaded filter lands filtered", async () => {
  location.hash = workspaceHash("sources", { q: "rss", chip: "Workspace" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("rss")).toBeTruthy();
  expect(screen.queryByText("notion")).toBeNull();
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("rss");
  expect(screen.getByRole("tab", { name: "Workspace" }).getAttribute("aria-selected")).toBe(
    "true",
  );
});

test("a tab click releases the filters, so returning starts unfiltered", async () => {
  location.hash = workspaceHash("sources", { q: "rss", chip: "Workspace" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("rss");

  await goTo("Team");
  await waitFor(() => expect(location.hash).toBe("#/workspace/team"));
  await goTo("Sources");

  await waitFor(() => expect(location.hash).toBe("#/workspace/sources"));
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
  expect(await screen.findByText("notion")).toBeTruthy();
});

test("closing the viewer unwinds the entry opening it pushed", async () => {
  location.hash = "#/agents";
  location.hash = sectionHash("artifacts");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await viewCard("report.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toContain("open=");
  await userEvent.click(screen.getByRole("button", { name: /^Close/ }));
  await waitFor(() => expect(location.hash).toBe("#/artifacts"));

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
  expect(screen.queryByText("file body")).toBeNull();
});

test("a cursor the surface refuses leaves a way back to the first page", async () => {
  location.hash = sectionHash("artifacts", { after: "not-a-cursor" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return json({ objects: [] });
      if (url.includes("after=")) return new Response("malformed listing cursor", { status: 400 });
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [NEWER], older: null });
      if (url.includes("/api/chats")) return json({ chats: [] });
      return json({ messages: [] });
    }),
  );
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "First page" }));

  expect(await viewCard("report.txt")).toBeTruthy();
  expect(location.hash).toBe("#/artifacts");
});

test("a second row opened behind the sheet still closes to the listing", async () => {
  location.hash = "#/agents";
  location.hash = sectionHash("artifacts", { after: "c-older" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();
  await userEvent.click(await viewCard("notes.txt"));
  await userEvent.click(screen.getByRole("button", { name: /^Close/ }));

  await waitFor(() => expect(location.hash).not.toContain("open="));
  expect(screen.queryByText("file body")).toBeNull();
  expect(location.hash).toContain("after=c-older");

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
});

test("paging away from an open row carries no dead open key", async () => {
  location.hash = sectionHash("artifacts");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await viewCard("report.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));

  expect(await viewCard("notes.txt")).toBeTruthy();
  expect(location.hash).not.toContain("open=");
  expect(screen.queryByText("That item is not on this page.")).toBeNull();
});

test("a placement from a pane the member already left never writes its dead place back", async () => {
  let release: ((value: Response) => void) | null = null;
  location.hash = workspaceHash("sources", { q: "rss" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/intents")) {
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      if (url.includes("/workspace/sources")) {
        return json({
          sources: [
            {
              name: "rss-feed",
              backend: "rss",
              stream: "feed",
              account_id: null,
              base_url: "https://example.com/feed",
              owner_email: null,
              shared: true,
              own: true,
              consecutive_errors: 0,
              next_sync_at: "2026-08-01T06:00:00",
            },
          ],
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return json({ members: [], can_add: false, domain: null });
    }),
  );
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));
  await waitFor(() => expect(release).not.toBeNull());
  await goTo("Team");
  await goTo("Sources");
  await waitFor(() => expect(location.hash).toBe("#/workspace/sources"));

  release!(json({ applied: true, message: "Resync queued." }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(location.hash).toBe("#/workspace/sources");
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
  expect(screen.queryByText("Resync queued.")).toBeNull();
});

test("a refused memory cursor leaves a way back to the first page", async () => {
  location.hash = workspaceHash("memory", { after: "not-a-cursor" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("after=")) return new Response("malformed listing cursor", { status: 400 });
      if (url.includes("/workspace/memory")) {
        return json({ available: true, kinds: ["fact"], matches: [] });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      return json({ messages: [] });
    }),
  );
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "First page" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/memory"));
  expect(await screen.findByText("No memories yet.")).toBeTruthy();
});

test("a hash naming a filter or a memory class that does not exist says so", async () => {
  location.hash = workspaceHash("sources", { chip: "Nonexistent" });
  serve();
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(await screen.findByText("That filter is not available.")).toBeTruthy();
  view.unmount();

  location.hash = workspaceHash("memory", { kind: "nonexistent" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  expect(await screen.findByText("That memory class is not available.")).toBeTruthy();
});

test("an intent resolving after the member leaves never rewrites where they went", async () => {
  let release: ((value: Response) => void) | null = null;
  location.hash = workspaceHash("sources");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/intents")) {
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      if (url.includes("/workspace/sources")) {
        return json({
          sources: [
            {
              name: "notion-main",
              backend: "notion",
              stream: "pages",
              account_id: "acct",
              base_url: null,
              owner_email: "member@example.com",
              shared: false,
              own: true,
              consecutive_errors: 0,
              next_sync_at: "2026-08-01T06:00:00",
            },
          ],
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return json({ members: [], can_add: false, domain: null });
    }),
  );
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));
  await waitFor(() => expect(release).not.toBeNull());
  await goTo("Team");
  await waitFor(() => expect(location.hash).toBe("#/workspace/team"));

  release!(json({ applied: true, message: "Resync queued." }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(location.hash).toBe("#/workspace/team");
});

test("a memory search and kind filter ride the hash and survive reload", async () => {
  location.hash = workspaceHash("memory");
  const calls = serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("tab", { name: "Fact" }));
  expect(location.hash).toBe("#/workspace/memory?kind=fact");
  await waitFor(() => expect(calls.some((url) => url.includes("kind=fact"))).toBe(true));

  await userEvent.type(screen.getByPlaceholderText("Search"), "roadmap{Enter}");
  expect(location.hash).toBe("#/workspace/memory?kind=fact&q=roadmap");
  await waitFor(() => expect(calls.some((url) => url.includes("q=roadmap"))).toBe(true));
});

test("a reloaded kind filter lands on that kind and reads it", async () => {
  location.hash = workspaceHash("memory", { kind: "fact" });
  const calls = serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const tab = await screen.findByRole("tab", { name: "Fact" });
  expect(tab.getAttribute("aria-selected")).toBe("true");
  expect(screen.getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe("false");
  expect(calls.some((url) => url.includes("/workspace/memory?kind=fact"))).toBe(true);
});

test("a search cleared from a kind returns to that kind rather than page one", async () => {
  location.hash = workspaceHash("memory", { kind: "fact", q: "roadmap" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const box = (await screen.findByPlaceholderText("Search")) as HTMLInputElement;
  expect(box.value).toBe("roadmap");
  await userEvent.clear(box);
  await userEvent.type(box, "{Enter}");

  await waitFor(() => expect(location.hash).toBe("#/workspace/memory?kind=fact"));
  expect(screen.getByRole("tab", { name: "Fact" }).getAttribute("aria-selected")).toBe("true");
});

/** The track store is the way back to a row of lanes, and the address states that row itself. A
 *  browser that refuses the write — a quota reached, a policy that blocks site storage — costs the
 *  member the way back, never the portal: the screen the link names still stands. */
test("a browser that refuses a storage write still mounts the portal", async () => {
  vi.stubGlobal("sessionStorage", {
    getItem: () => null,
    setItem: () => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    },
    removeItem: () => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    },
    clear: () => {},
  });
  const link = sectionHash("artifacts", { after: "c-older", opens: [OLDER_KEY] });
  location.hash = link;
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toBe(link);
});

/** An id the shelf cannot draw a file for still stands as a lane and says so inside it: the close
 *  is the lane's own, and an address the member cannot shut is one they cannot leave. */
test("a deep link naming a row that is not on this page says so, in a lane that closes", async () => {
  const gone = "2020-01-01T00:00:00|gone.txt";
  location.hash = sectionHash("artifacts", { opens: [gone] });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const lane = await screen.findByRole("region", { name: gone });
  expect(within(lane).getByText("That item is not on this page.")).toBeTruthy();
  expect(await viewCard("report.txt")).toBeTruthy();

  await userEvent.click(within(lane).getByRole("button", { name: /^Close/ }));

  await waitFor(() => expect(location.hash).toBe("#/artifacts"));
  expect(screen.queryByText("That item is not on this page.")).toBeNull();
});

test("opening and closing the viewer issues no second listing read", async () => {
  location.hash = sectionHash("artifacts");
  const calls = serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await viewCard("report.txt");
  const reads = () => calls.filter((url) => url.includes("/workspace/artifacts")).length;
  const before = reads();

  await userEvent.click(await viewCard("report.txt"));
  expect(await screen.findByText("file body")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: /^Close/ }));
  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());

  expect(reads()).toBe(before);
});

test("a reloaded memory search prefills the box and fetches the query", async () => {
  location.hash = workspaceHash("memory", { q: "roadmap" });
  const calls = serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByDisplayValue("roadmap")).toBeTruthy();
  expect(calls.some((url) => url.includes("/workspace/memory?q=roadmap"))).toBe(true);
});

test("an artifact named as a query target is honored only inside its namespace", () => {
  expect(artifactTarget("?a=%2Fartifacts%2Fabc%2Fchart.png%3Fexp%3D1%26sig%3Dx")).toBe(
    "/artifacts/abc/chart.png?exp=1&sig=x",
  );
  expect(artifactTarget("?a=https%3A%2F%2Fevil.example%2F")).toBeNull();
  expect(artifactTarget("?a=%2Fsurface%2Fweb")).toBeNull();
  expect(artifactTarget("")).toBeNull();
});
