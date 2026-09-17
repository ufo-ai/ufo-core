import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import type { Placement } from "@/kernel/pager";
import { usePlaceRecorder } from "@/kernel/place";
import { HOME_NEW_LANE, homeLaneAgent, mintHomeLane } from "@/lib/homeLanes";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";
import {
  AGENTS_HASH,
  agentsHash,
  BUILDER_HASH,
  STORE_HASH,
  automationsHash,
  FIRST_RUN_HASH,
  agentHash,
  artifactTarget,
  bootRoute,
  chatHash,
  CONNECTION_TABS,
  conversationSlotHash,
  firstRunHash,
  homeHash,
  parseHash,
  routeIs,
  sectionHash,
  standing,
  workspaceHash,
  type PlaceStep,
  type WorkspacePlace,
} from "@/lib/route";

import { AGENT, CHAT_ROW, chatsOnWire, CONVO_ID, goTo, json, MEMBER, TURN_ID, useStreamFake, wire } from "./harness";

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

const NO_TOKENS = { tokens: 0, token_micro_usd: 0, total_micro_usd: 0 };

const NO_USAGE = {
  window_seconds: null,
  total_micro_usd: 0,
  by_dimension: [],
  caps: [],
  usage: {
    selected: NO_TOKENS,
    all_time: NO_TOKENS,
    first_used_at: null,
    previous_tokens: null,
    daily: [],
    by_execution: [],
    by_model: [],
  },
  workspace: null,
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
        return json({ available: true, actions: [], kinds: ["fact", "profile"], matches: [] });
      }
      if (url.includes("/workspace/credentials")) {
        return json({
          slots: [
            {
              name: "openai_api_key",
              slot: "openai_api_key",
              description: "OpenAI API key",
              extension: "models",
              filled: true,
            },
          ],
        });
      }
      if (url.includes("/workspace/team")) {
        return json({ members: [MEMBER], can_add: false, actions: [] });
      }
      if (url.includes("/workspace/usage")) return json(NO_USAGE);
      if (url.includes("/objects/conversation/")) {
        return new Response("no such conversation", { status: 404 });
      }
      if (url.includes("/objects/conversation")) return json({ objects: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return new Response("file body");
    }),
  );
  return calls;
}

test("a workspace tab and a section carry their place and parse back to it", () => {
  const place = {
    kind: "fact",
    after: "c2",
    q: "roadmap",
    chip: "Workspace",
    face: "table",
    scope: "mine",
    range: "7d",
    runs: "older",
    opens: [OLDER_KEY],
  };
  expect(parseHash(workspaceHash("memory", place))).toEqual({
    kind: "workspace",
    view: "memory",
    place,
  });
  expect(parseHash(sectionHash("connectors", place))).toEqual({
    kind: "section",
    section: "connectors",
    place,
  });
  expect(parseHash("#/artifacts")).toEqual({ kind: "section", section: "artifacts", place: {} });
  expect(parseHash("#/sites")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/memory")).toEqual({ kind: "bad-link" });
});

test("the app list is a workspace tab, and its chip rides the address", () => {
  expect(workspaceHash("apps")).toBe("#/workspace/apps");
  expect(workspaceHash("apps", { chip: "Archived" })).toBe("#/workspace/apps?chip=Archived");
  expect(parseHash("#/workspace/apps?chip=Archived")).toEqual({
    kind: "workspace",
    view: "apps",
    place: { chip: "Archived" },
  });
});

test("the apps address stands on no workspace row, and its workspace tab does", () => {
  expect(standing(parseHash(AGENTS_HASH), "workspace")).toBe(false);
  expect(standing(parseHash("#/workspace/apps"), "workspace")).toBe(true);
  expect(standing(parseHash("#/workspace/team"), "workspace")).toBe(true);
});

test("a screen moved off the workspace tabs still answers at the address it had", () => {
  expect(parseHash("#/workspace/wiki?q=slack")).toEqual({
    kind: "section",
    section: "wiki",
    place: { q: "slack" },
  });
  expect(parseHash("#/workspace/artifacts")).toEqual({
    kind: "section",
    section: "artifacts",
    place: {},
  });
  expect(parseHash("#/workspace/nothing")).toEqual({ kind: "bad-link" });
});

test("every connections screen has one section address", () => {
  expect(parseHash("#/workspace/sources")).toEqual({ kind: "bad-link" });
  for (const tab of CONNECTION_TABS) {
    expect(parseHash("#/workspace/" + tab)).toEqual({ kind: "bad-link" });
    expect(parseHash("#/" + tab)).toEqual({ kind: "section", section: tab, place: {} });
  }
});

test("an address naming an inherited property of an object names no workspace tab", () => {
  for (const name of [
    "constructor",
    "__proto__",
    "toString",
    "valueOf",
    "hasOwnProperty",
    "isPrototypeOf",
  ]) {
    expect(parseHash("#/workspace/" + name)).toEqual({ kind: "bad-link" });
    expect(parseHash("#/workspace/" + name + "?q=slack")).toEqual({ kind: "bad-link" });
  }
  expect(parseHash("#/constructor")).toEqual({ kind: "bad-link" });
});

test("the automations screen carries one place, and the workspace holds none of it", () => {
  expect(parseHash(automationsHash())).toEqual({ kind: "automations", place: {} });
  expect(parseHash(automationsHash() + "?runs=older&open=run%2Fa%2Fturn1")).toEqual({
    kind: "automations",
    place: { runs: "older", opens: ["run/a/turn1"] },
  });
  expect(
    parseHash(
      automationsHash() +
        "?open=automation%2Fa%2Fscheduled_task%2Fnightly~run%2Fa%2Fscheduled_task%2Fnightly%2Fturn1",
    ),
  ).toEqual({
    kind: "automations",
    place: {
      opens: ["automation/a/scheduled_task/nightly", "run/a/scheduled_task/nightly/turn1"],
    },
  });
  expect(parseHash(automationsHash({ opens: ["object/a/scheduled_task/nightly"] }))).toEqual({
    kind: "automations",
    place: { opens: ["object/a/scheduled_task/nightly"] },
  });
  expect(parseHash("#/workspace/automations")).toEqual({ kind: "bad-link" });
});

test("the automations screen is one address, carrying both its list pages and its open lane", () => {
  expect(parseHash("#/automations?after=page2&runs=older")).toEqual({
    kind: "automations",
    place: { after: "page2", runs: "older" },
  });
  expect(automationsHash({ opens: ["run/a/" + OLDER_KEY], runs: "older" })).toBe(
    "#/automations?runs=older&open=run%2Fa%2F" + OLDER_KEY,
  );
  expect(automationsHash({ opens: ["automation/a/scheduled_task/nightly"] })).toBe(
    "#/automations?open=automation%2Fa%2Fscheduled_task%2Fnightly",
  );
  expect(
    automationsHash({
      opens: ["automation/a/scheduled_task/nightly", "object/a/scheduled_task/nightly"],
    }),
  ).toBe(
    "#/automations?open=automation%2Fa%2Fscheduled_task%2Fnightly%7Eobject%2Fa%2Fscheduled_task%2Fnightly",
  );
});

test("a conversation slot has a builder, and it writes the address its own read takes", () => {
  const root = "99999999-9999-4999-8999-999999999999";
  expect(conversationSlotHash(AGENT.id, CONVO_ID, "changes")).toBe(
    "#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/changes",
  );
  expect(parseHash(conversationSlotHash(AGENT.id, CONVO_ID, "changes", root))).toEqual({
    kind: "conversation-slot",
    agentId: AGENT.id,
    conversationId: CONVO_ID,
    slot: "changes",
    rootConversationId: root,
  });
});

test("a chat address names the run it stands on, and refuses a run that is not a turn", () => {
  expect(chatHash(CONVO_ID, undefined, undefined, TURN_ID)).toBe(
    "#/c/" + CONVO_ID + "?run=" + TURN_ID,
  );
  expect(parseHash(chatHash(CONVO_ID, undefined, undefined, TURN_ID))).toEqual({
    kind: "chat",
    conversationId: CONVO_ID,
    run: TURN_ID,
  });
  expect(parseHash(chatHash(CONVO_ID, "changes", undefined, TURN_ID))).toEqual({
    kind: "chat",
    conversationId: CONVO_ID,
    slot: "changes",
    run: TURN_ID,
  });
  expect(parseHash("#/c/" + CONVO_ID + "?run=nightly-digest")).toEqual({
    kind: "chat",
    conversationId: CONVO_ID,
  });
  expect(parseHash(chatHash(CONVO_ID))).toEqual({ kind: "chat", conversationId: CONVO_ID });
});

test("a screen that carries no place is read whatever the address arrived holding", () => {
  expect(parseHash(BUILDER_HASH + "?x=1")).toEqual({ kind: "agents", build: true, place: {} });
  expect(parseHash(STORE_HASH + "?x=1")).toEqual({ kind: "store" });
  expect(parseHash(FIRST_RUN_HASH + "?x=1")).toEqual({ kind: "first-run" });
  expect(parseHash("")).toEqual({ kind: "home", place: {} });
  expect(parseHash("#")).toEqual({ kind: "home", place: {} });
  expect(parseHash("#/")).toEqual({ kind: "home", place: {} });
});

test("the apps screen carries its place, and a filter rides its address", () => {
  expect(agentsHash()).toBe(AGENTS_HASH);
  expect(parseHash(AGENTS_HASH + "?x=1")).toEqual({ kind: "agents", place: {} });

  const filtered: WorkspacePlace = { chip: "Archived" };
  expect(agentsHash(filtered)).toBe(AGENTS_HASH + "?chip=Archived");
  expect(parseHash(agentsHash(filtered))).toEqual({ kind: "agents", place: filtered });
});

test("home carries its lanes, and the bare address is home standing none", () => {
  expect(parseHash("#/?ref=mail")).toEqual({ kind: "home", place: {} });

  const lanes: WorkspacePlace = { opens: [AGENT.id, HOME_NEW_LANE] };
  expect(homeHash()).toBe("#/");
  expect(homeHash(lanes)).toBe("#/?open=" + AGENT.id + "%7E" + HOME_NEW_LANE);
  expect(parseHash(homeHash(lanes))).toEqual({ kind: "home", place: lanes });
  expect(parseHash("#/?open=a~~b")).toEqual({ kind: "bad-link" });
});

test("a home lane names the app it stands, and the instance of it", () => {
  expect(mintHomeLane(AGENT.id, [])).toBe(AGENT.id);
  expect(mintHomeLane(AGENT.id, [HOME_NEW_LANE])).toBe(AGENT.id);
  expect(mintHomeLane(AGENT.id, [AGENT.id])).toBe(AGENT.id + ".2");
  expect(mintHomeLane(AGENT.id, [AGENT.id, AGENT.id + ".2"])).toBe(AGENT.id + ".3");
  expect(mintHomeLane(AGENT.id, [AGENT.id, AGENT.id + ".3"])).toBe(AGENT.id + ".2");

  expect(homeLaneAgent(AGENT.id)).toBe(AGENT.id);
  expect(homeLaneAgent(AGENT.id + ".2")).toBe(AGENT.id);
  expect(homeLaneAgent(HOME_NEW_LANE)).toBeNull();
  expect(parseHash(homeHash({ opens: [mintHomeLane(AGENT.id, [AGENT.id])] }))).toEqual({
    kind: "home",
    place: { opens: [AGENT.id + ".2"] },
  });
});

test("a route answers which kind it is, and narrows to it", () => {
  const route = parseHash(chatHash(CONVO_ID));
  expect(routeIs(route, "chat")).toBe(true);
  expect(routeIs(route, "home")).toBe(false);
  expect(routeIs(route, "chat") && route.conversationId).toBe(CONVO_ID);
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
  expect(sectionHash("connectors", none)).toBe("#/connectors?after=c-older");
  expect(parseHash(sectionHash("connectors", none))).toEqual({
    kind: "section",
    section: "connectors",
    place: none,
  });

  const one: WorkspacePlace = { opens: [OLDER_KEY] };
  expect(parseHash(sectionHash("connectors", one))).toEqual({
    kind: "section",
    section: "connectors",
    place: one,
  });

  const many: WorkspacePlace = { opens: [CONVO_ID, AGENT.id, OLDER_KEY] };
  expect(sectionHash("connectors", many)).toBe(
    "#/connectors?open=" + CONVO_ID + "%7E" + AGENT.id + "%7E" + OLDER_KEY,
  );
  expect(parseHash(sectionHash("connectors", many))).toEqual({
    kind: "section",
    section: "connectors",
    place: many,
  });
});

test("a link naming one thing to open stands on the screen as a track of one", () => {
  expect(parseHash("#/connectors?open=" + OLDER_KEY)).toEqual({
    kind: "section",
    section: "connectors",
    place: { opens: [OLDER_KEY] },
  });
  expect(parseHash("#/agents/" + AGENT.id + "?open=" + CONVO_ID + "&q=roadmap")).toEqual({
    kind: "agent",
    agentId: AGENT.id,
    place: { q: "roadmap", opens: [CONVO_ID] },
  });
});

test("a mangled track names no route, and never a screen quietly missing its slots", () => {
  expect(parseHash("#/connectors?open=a~~b")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/connectors?open=~")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/connectors?open=~" + CONVO_ID)).toEqual({ kind: "bad-link" });
  expect(parseHash("#/connectors?open=" + CONVO_ID + "~")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/workspace/team?open=" + CONVO_ID + "~~" + AGENT.id)).toEqual({
    kind: "bad-link",
  });
  expect(parseHash("#/agents/" + AGENT.id + "?q=roadmap&open=a~~b")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/connectors?open=a%0Ab")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/connectors?open=" + "x".repeat(300))).toEqual({ kind: "bad-link" });
});

/** One id in a track twice is two hosts over one record, and a row longer than the store holds is lanes
 *  the member would lose on the way back. */
test("a track naming a slot twice, or more lanes than a screen holds, names no route", () => {
  const lanes = (count: number) =>
    Array.from({ length: count }, (_, at) => "object/report/" + at).join("~");

  expect(parseHash("#/connectors?open=a~b~a")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/agents/" + AGENT.id + "?open=" + CONVO_ID + "~" + CONVO_ID)).toEqual({
    kind: "bad-link",
  });
  expect(parseHash("#/connectors?open=" + lanes(25))).toEqual({ kind: "bad-link" });
  expect(parseHash("#/connectors?open=" + lanes(24))).toEqual({
    kind: "section",
    section: "connectors",
    place: { opens: lanes(24).split("~") },
  });
});

test("a track no address can carry raises where it was made up", () => {
  expect(() => sectionHash("connectors", { opens: [CONVO_ID + "~" + AGENT.id] })).toThrow(/slot id/);
  expect(() => sectionHash("connectors", { opens: [""] })).toThrow(/slot id/);
  expect(() => agentHash(AGENT.id, { opens: [CONVO_ID, "a~b"] })).toThrow(/slot id/);
  expect(() => sectionHash("connectors", { opens: ["x".repeat(300)] })).toThrow(/slot id/);
  expect(() => sectionHash("connectors", { opens: [CONVO_ID, CONVO_ID] })).toThrow(/twice/);
  expect(() =>
    sectionHash("connectors", {
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

test("a place change holds the keys its patch does not name", () => {
  const { steps, places, record } = stepping({ range: "7d", chip: "Workspace" });

  record({ opens: [CONVO_ID] });
  record({ q: "roadmap" });
  record({ after: "c-older" });

  expect(steps).toEqual(["push", "replace", "push"]);
  expect(places).toEqual([
    { range: "7d", chip: "Workspace", opens: [CONVO_ID] },
    { range: "7d", chip: "Workspace", q: "roadmap", opens: [CONVO_ID] },
    { range: "7d", chip: "Workspace", q: "roadmap", after: "c-older", opens: [CONVO_ID] },
  ]);
});

test("a hash under the torn-out subagent namespace names no route", () => {
  expect(parseHash("#/subagents/deep_research")).toEqual({ kind: "bad-link" });
  expect(parseHash("#/subagents/deep_research/conversations/" + CONVO_ID)).toEqual({
    kind: "bad-link",
  });
});

test("a conversation named as a query target boots on that conversation", () => {
  expect(bootRoute("", "?c=" + CONVO_ID)).toEqual({ kind: "chat", conversationId: CONVO_ID });
  expect(bootRoute("#/agents", "?c=" + CONVO_ID)).toEqual({ kind: "agents", place: {} });
  expect(bootRoute("", "")).toEqual({ kind: "home", place: {} });
});

test("the sign-in that founded the workspace boots on its opening chat", () => {
  expect(bootRoute("", "?first=1")).toEqual({ kind: "first-run" });
  expect(parseHash("#/first-run")).toEqual({ kind: "first-run" });
  expect(bootRoute("#/first-run", "")).toEqual({ kind: "first-run" });
  expect(firstRunHash()).toBe(FIRST_RUN_HASH);
  expect(firstRunHash("slack")).toBe("#/first-run/slack");
  expect(parseHash(firstRunHash("slack"))).toEqual({ kind: "first-run", step: "slack" });
  expect(parseHash("#/first-run/slack?x=1")).toEqual({ kind: "first-run", step: "slack" });
  expect(bootRoute("#/first-run/slack", "?first=1")).toEqual({ kind: "first-run", step: "slack" });
  expect(parseHash("#/first-run/Slack")).toEqual({ kind: "bad-link" });
  expect(bootRoute("", "?first=1&c=" + CONVO_ID)).toEqual({
    kind: "chat",
    conversationId: CONVO_ID,
  });
  expect(bootRoute("#/agents", "?first=1")).toEqual({ kind: "agents", place: {} });
  expect(bootRoute("", "")).toEqual({ kind: "home", place: {} });
});

test("a conversation permalink that is not a lowercase uuid names no route", () => {
  expect(parseHash(chatHash(MIXED_CASE_CONVO_ID))).toEqual({ kind: "bad-link" });
  expect(parseHash("#/c/not-a-conversation")).toEqual({ kind: "bad-link" });
  expect(bootRoute("", "?c=" + MIXED_CASE_CONVO_ID)).toEqual({ kind: "bad-link" });
});

test("the conversation a sign-in carried through opens, and the hash names it", async () => {
  history.replaceState(null, "", location.pathname + "?c=" + CONVO_ID);
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/transcript": () => json({ messages: [] }),
    "/slots": () => json({ slots: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(location.hash).toBe(chatHash(CONVO_ID));
});

test("an address the portal cannot read reports a bad link, whichever part is mangled", async () => {
  history.replaceState(null, "", chatHash(MIXED_CASE_CONVO_ID));
  wire({ "/transcript": () => json({ messages: [] }) });
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  expect(screen.queryByLabelText("Ask UFO")).toBeNull();
  view.unmount();

  history.replaceState(null, "", "#/connectors?open=a~~b");
  const mangled = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  mangled.unmount();

  history.replaceState(null, "", "#/workspace/__proto__");
  const inherited = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
  inherited.unmount();

  history.replaceState(null, "", "#/workspace/constructor");
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("This link is not valid.")).toBeTruthy();
});

test("a search rides the hash by replacement, never as a history entry", async () => {
  location.hash = "#/agents";
  location.hash = workspaceHash("memory");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByRole("searchbox"), "openai{enter}");

  await waitFor(() => expect(location.hash).toContain("q=openai"));

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
});

test("a filter chip rides the hash by replacement, never as a history entry", async () => {
  location.hash = workspaceHash("team");
  location.hash = workspaceHash("apps");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("tab", { name: "Archived" }));

  await waitFor(() => expect(location.hash).toContain("chip=Archived"));

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/workspace/team"));
});

test("a search term typed and not submitted does not follow the member to the next tab", async () => {
  location.hash = workspaceHash("team");
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await goTo("Memory");
  await userEvent.type(await screen.findByLabelText("Search memory"), "roadmap");
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("roadmap");
  expect(location.hash).not.toContain("q=");

  await goTo("Team");

  const box = (await screen.findByLabelText("Search members")) as HTMLInputElement;
  expect(box.value).toBe("");
});

test("a tab click releases the filters, so returning starts unfiltered", async () => {
  location.hash = workspaceHash("memory", { q: "roadmap", kind: "fact" });
  serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("roadmap");

  await goTo("Team");
  await waitFor(() => expect(location.hash).toBe("#/workspace/team"));
  await goTo("Memory");

  await waitFor(() => expect(location.hash).toBe("#/workspace/memory"));
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
  expect(await screen.findByText("No memories yet.")).toBeTruthy();
});

test("the usage range rides the address, and a link naming one lands on it", async () => {
  location.hash = workspaceHash("usage");
  const calls = serve();
  const view = render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "7 days" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/usage?range=7d"));
  await waitFor(() =>
    expect(calls.some((url) => url.includes("/workspace/usage?range=7d"))).toBe(true),
  );
  view.unmount();

  location.hash = workspaceHash("usage", { range: "90d" });
  const reloaded = serve();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pressed = await screen.findByRole("button", { name: "90 days" });
  expect(pressed.getAttribute("aria-pressed")).toBe("true");
  expect(reloaded.some((url) => url.includes("/workspace/usage?range=90d"))).toBe(true);
});

test("a refused memory cursor leaves a way back to the first page", async () => {
  location.hash = workspaceHash("memory", { after: "not-a-cursor" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("after=")) return new Response("malformed listing cursor", { status: 400 });
      if (url.includes("/workspace/memory")) {
        return json({ available: true, actions: [], kinds: ["fact"], matches: [] });
      }
      if (url.includes("/workspace/surfaces")) return json({ surfaces: [] });
      if (url.includes("/objects/conversation/")) {
        return new Response("no such conversation", { status: 404 });
      }
      if (url.includes("/objects/conversation")) return json({ objects: [] });
      return json({ messages: [] });
    }),
  );
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "First page" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/memory"));
  expect(await screen.findByText("No memories yet.")).toBeTruthy();
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
