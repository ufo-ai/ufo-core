import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  PlacedWorkspace,
  SECOND,
  NO_SITES,
  SITE_KIND,
  StreamFake,
  TASK_KIND,
  TURN_ID,
  fact,
  json,
  objectIndex,
  opened,
  pick,
  refusedNotice,
  useStreamFake,
  viewCard,
  wire,
} from "./harness";
beforeEach(() => {
  useStreamFake();
});

const OVERVIEW = {
  agent: {
    name: "assistant",
    main: true,
    surfaces: ["web"],
    updated_at: "2026-07-30T12:00:00",
    prompt: "be useful",
    prompt_digest: "abc123",
  },
  spec: { model: "opus", reasoning: "high" },
  spec_schema: {
    properties: {
      model: { type: "string" },
      reasoning: { type: "string", enum: ["low", "high"] },
      internet_access_allowed: { type: "boolean" },
    },
  },
  models: ["opus", "sonnet"],
  deploy: { sandbox_internet: true },
  audience: [],
};

test("the overview states the agent's facts, renders its schema, and submits a settings intent", async () => {
  const posted: unknown[] = [];
  wire({
    "/overview": () => json(OVERVIEW),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("Main agent")).toBeTruthy();
  expect(fact("Installations")).toBe("web");
  expect(fact("Updated")).toBe("Jul 30 2026");
  expect(fact("Prompt digest")).toBe("abc123");
  expect(fact("Web audience")).toBe("Every member");
  expect(screen.getByText("be useful")).toBeTruthy();

  await pick("reasoning", "low");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { reasoning: "low", model: "opus" },
  });
  expect(await screen.findByText("Applied.")).toBeTruthy();
});

test("an overview that fails to read states the error and offers no form", async () => {
  wire({
    "/overview": () => new Response("nope", { status: 503 }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("the task index lists declared fields and its detail pauses through the intent lane", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task/digest": () =>
      json({
        ...TASK_KIND,
        name: "digest",
        summary: "0 9 * * * — summarize",
        spec: { schedule: "0 9 * * *", prompt: "summarize", paused: false },
        status: { next_run_at: "2026-08-01T09:00:00Z", paused: false },
        links: [],
        created_at: "2026-07-01T09:00:00Z",
        updated_at: "2026-07-01T09:00:00Z",
      }),
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        {
          name: "digest",
          summary: "0 9 * * * — summarize",
          next_run_at: "2026-08-01T09:00:00Z",
          paused: false,
        },
      ]),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Paused digest." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/tasks";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  const listed = (await screen.findByRole("button", { name: "digest" })).closest("tr");
  const said = [...(listed?.querySelectorAll("td") ?? [])].map((box) => String(box.textContent));
  expect(said[1]).toBe("0 9 * * * — summarize");
  expect(said[2]).toContain(" ago");
  expect(said[3]).toBe("No");

  await userEvent.click(screen.getByRole("button", { name: "digest" }));
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  await userEvent.click(await screen.findByLabelText("paused"));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "scheduled_task",
    name: "digest",
    spec: { paused: true },
  });
});

test("a detail whose kind the lane refuses offers no control and no prose about it", async () => {
  wire({
    "/objects/site/docs-abc": () =>
      json({
        ...SITE_KIND,
        name: "docs-abc",
        summary: "docs · workspace · sandbox port 3000",
        spec: { visibility: "workspace" },
        status: { conversation: CONVO_ID, created_at: "2026-07-01T09:00:00Z", visibility: "workspace" },
        links: [],
        created_at: "2026-07-01T09:00:00Z",
        updated_at: null,
      }),
    "/objects/site": () =>
      objectIndex(SITE_KIND, [
        {
          name: "docs-abc",
          summary: "docs · workspace · sandbox port 3000",
          conversation: CONVO_ID,
          created_at: "2026-07-01T09:00:00Z",
          visibility: "workspace",
        },
      ]),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/workspace/sites";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await viewCard("docs-abc"));

  expect(await screen.findByRole("heading", { name: "docs-abc" })).toBeTruthy();
  expect(document.querySelector('[data-part="refusal"]')).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("skills name where each came from, and save posts one skill file", async () => {
  const posted: unknown[] = [];
  wire({
    "/skills": () =>
      json({
        skills: [
          { name: "mine", description: "member skill", origin: "member" },
          { name: "shipped", description: "deploy skill", origin: "deploy" },
        ],
      }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("member skill")).toBeTruthy();
  expect(screen.getByText("deploy skill")).toBeTruthy();
  expect(screen.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Name",
    "Description",
    "Source",
    "",
  ]);
  expect(screen.getByText("mine").closest("tr")?.textContent).toContain("This agent");
  expect(screen.getByText("shipped").closest("tr")?.textContent).toContain("Deploy");

  await userEvent.click(screen.getByRole("tab", { name: "Deploy" }));
  expect(screen.queryByText("member skill")).toBeNull();
  expect(screen.getByText("deploy skill")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();

  await userEvent.click(screen.getByRole("tab", { name: "This agent" }));
  expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "All" }));
  await userEvent.click(screen.getByRole("button", { name: "New skill" }));
  await userEvent.type(await screen.findByLabelText("Name"), "fresh");
  await userEvent.type(screen.getByLabelText("Description"), "Load when: asked.");
  await userEvent.type(screen.getByLabelText("Instructions"), "body");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "skill",
    name: "fresh",
    spec: {
      files: {
        "SKILL.md": '---\nname: fresh\ndescription: "Load when: asked."\n---\n\nbody\n',
      },
    },
  });
});

test("connections flip and revoke a grant, and a 404 usage read says it is not shared", async () => {
  const posted: unknown[] = [];
  wire({
    "/connections": () =>
      json({
        connections: [
          {
            provider: "github",
            account_id: "acct",
            owner_email: "member@example.com",
            shared: false,
            connected_at: "2026-07-01T00:00:00",
            grant: "g1",
          },
        ],
      }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Shared." });
    },
    "/usage": () => new Response("no", { status: 404 }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/connections";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("Private")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "apply", kind: "connector_grant", name: "g1" });

  await userEvent.click(screen.getByRole("tab", { name: "Usage" }));
  expect(await screen.findByText("Usage for this agent is not shared with you.")).toBeTruthy();
});

test("a workspace-shared conversation reads as shared, in its row and its detail heading", async () => {
  const shared = "5c0be3aa-0000-4000-8000-000000000003";
  wire({
    "/turns": () => json({ turns: [], subagent_turns: [] }),
    "/files": () => json({ files: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: shared,
            surface: "slack",
            member_email: null,
            turn_count: 3,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: null,
            readable: true,
            disclosable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  const label = "Shared · " + shared.slice(0, 8);
  const row = (await screen.findByText("slack")).closest("tr")!;
  expect(row.querySelector("td")!.textContent).toBe(label);
  expect(row.textContent).not.toContain("Channel or room");

  await userEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(await screen.findByText("slack · " + label)).toBeTruthy();
});

test("conversations open a turn tree that nests a subagent under the turn that spawned it", async () => {
  const rootConversation = "11111111-1111-4111-8111-111111111111";
  wire({
    ["/conversations/" + rootConversation + "/turns"]: () =>
      json({
        turns: [
          {
            id: "t1",
            agent_id: AGENT_ID,
            conversation_id: rootConversation,
            seq: 2,
            status: "done",
            created_at: "2026-07-30T10:00:00",
            inbound: "parent ask",
            outcome: "parent answer",
            error_class: null,
            subagent_profile: null,
            parent_turn_id: null,
          },
          {
            id: "t3",
            agent_id: AGENT_ID,
            conversation_id: rootConversation,
            seq: 3,
            status: "done",
            created_at: "2026-07-30T10:00:02",
            inbound: "parent follow-up",
            outcome: "follow-up answer",
            error_class: null,
            subagent_profile: null,
            parent_turn_id: null,
          },
        ],
        subagent_turns: [
          {
            id: "t2",
            agent_id: AGENT_ID,
            conversation_id: "22222222-2222-4222-8222-222222222222",
            seq: 1,
            status: "done",
            created_at: "2026-07-30T10:00:01",
            inbound: "child ask",
            outcome: "child answer",
            error_class: null,
            subagent_profile: "research",
            parent_turn_id: "t1",
          },
        ],
      }),
    ["/conversations/" + rootConversation + "/files"]: () => json({ files: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: rootConversation,
            surface: "web",
            member_email: "member@example.com",
            turn_count: 2,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: "2026-07-30T10:00:01",
            readable: true,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(await screen.findByRole("button", { name: "Open" }));

  expect(await screen.findByText("parent ask")).toBeTruthy();
  expect(screen.getByText("child answer")).toBeTruthy();
  const parent = screen.getByText(/^turn 2 ·/);
  const child = screen.getByText(/^subagent research ·/);
  expect(parent.compareDocumentPosition(child) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const changes = screen.getAllByRole("link", { name: /^Changes from/ });
  expect(changes.map((link) => link.getAttribute("aria-label"))).toEqual([
    "Changes from turn 2",
    "Changes from subagent research",
  ]);
  expect(changes.map((link) => link.getAttribute("href"))).toEqual([
    "#/agents/" +
      AGENT_ID +
      "/conversations/" +
      rootConversation +
      "/slots/changes",
    "#/agents/" +
      AGENT_ID +
      "/conversations/22222222-2222-4222-8222-222222222222/slots/changes?root=" +
      rootConversation,
  ]);
  expect(await screen.findByText("No files in this conversation's workspace.")).toBeTruthy();
});

test("a conversation nobody shared offers no opener", async () => {
  wire({
    "/conversations": () =>
      json({
        conversations: [
          {
            id: "7ae41c02-0000-4000-8000-000000000001",
            surface: "slack",
            member_email: null,
            turn_count: 4,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: null,
            readable: false,
          },
          {
            id: "31bd9f77-0000-4000-8000-000000000002",
            surface: "slack",
            member_email: null,
            turn_count: 2,
            created_at: "2026-07-30T11:00:00",
            last_turn_at: null,
            readable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect((await screen.findAllByText("not shared with you")).length).toBe(2);
  expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
  const rows = screen.getAllByText("slack").map((cell) => cell.closest("tr")!);
  const labels = rows.map((row) => row.querySelector("td")!.textContent ?? "");
  expect(labels.every((label) => label.startsWith("Channel or room · "))).toBe(true);
  expect(new Set(labels).size).toBe(labels.length);
  expect(labels.some((label) => label.startsWith("Shared"))).toBe(false);
});

test("the sources listing groups a binding's streams and acts on the main agent's lane", async () => {
  const posted: { url: string; body: unknown }[] = [];
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            consecutive_errors: 1,
            next_sync_at: "2026-08-01T06:00:00",
          },
          {
            name: "notion-main",
            backend: "notion",
            stream: "databases",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            consecutive_errors: 2,
            next_sync_at: "2026-08-01T07:00:00",
          },
        ],
      }),
    "/intents": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: false, message: "Refused." });
    },
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("databases, pages")).toBeTruthy();
  expect(screen.getByText("3")).toBeTruthy();
  expect(screen.getByText("Aug 1 2026")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Resync" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].body).toMatchObject({
    verb: "apply",
    kind: "source",
    name: "notion-main",
    spec: { resync: true, streams: ["databases", "pages"] },
  });
  expect(await screen.findByText("Refused.")).toBeTruthy();
});

test("a workspace usage read shows the member's own spend and the admin rollup when present", async () => {
  wire({
    "/workspace/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 1_500_000,
        by_dimension: [{ dimension: "tokens", amount: 1200, priced_micro_usd: 1_500_000 }],
        caps: [{ window_seconds: 86_400, limit_micro_usd: 4_000, on_breach: "park" }],
        workspace: {
          total_micro_usd: 9_000_000,
          by_dimension: [],
          by_member: [{ label: "member@example.com", priced_micro_usd: 9_000_000 }],
          by_agent: [],
        },
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("You")).toBeTruthy();
  expect(screen.getAllByText("$1.50").length).toBe(2);
  expect(screen.getAllByText("$9.00").length).toBe(2);
  expect(screen.getAllByText("Last 24 hours").length).toBe(2);
  expect(screen.getByText("Model tokens")).toBeTruthy();
  expect(screen.getByText("1,200")).toBeTruthy();
  expect(screen.getByText("<$0.01")).toBeTruthy();
  expect(screen.getByText("Suspend the turn")).toBeTruthy();
  expect(screen.getByText("member@example.com")).toBeTruthy();
});

test("a member with no rollup sees only their own figure and no workspace section", async () => {
  wire({
    "/workspace/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 1_500_000,
        by_dimension: [{ dimension: "egress", amount: 4, priced_micro_usd: 0 }],
        caps: [],
        workspace: null,
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("Your spend")).toBeTruthy();
  expect(screen.getByText("Sandbox requests")).toBeTruthy();
  expect(screen.queryByText("Workspace spend")).toBeNull();
  expect(screen.queryByText("Spend by member")).toBeNull();
});

test("the sidebar routes agents and the workspace by hash and marks the section selected", async () => {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/overview": () => json(OVERVIEW),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  expect(location.hash).toBe("#/agents");
  expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-current")).toBe("true");

  await userEvent.click(await viewCard("second"));
  expect(location.hash).toBe("#/agents/" + SECOND.id);
  expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-current")).toBe("true");

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  expect(location.hash).toBe("#/workspace/team");
  await userEvent.click(screen.getByRole("tab", { name: "Sites" }));
  expect(location.hash).toBe("#/workspace/sites");
  expect(await screen.findByText(NO_SITES)).toBeTruthy();
});

test("the agents view lists the deploy's subagents below the agents, opening nothing", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(
    <App
      agents={[AGENT]}
      subagents={[
        { name: "deep_research", model: "claude-opus-4-8" },
        { name: "general_purpose", model: null },
      ]}
      member={MEMBER}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  const index = within(screen.getByRole("main"));
  const cards = index.getAllByRole("listitem");
  expect(cards.map((card) => within(card).getByText(/^(assistant|deep_research|general_purpose)$/).textContent)).toEqual([
    "assistant",
    "deep_research",
    "general_purpose",
  ]);
  expect(within(cards[0]).getAllByRole("button").map((button) => button.textContent)).toEqual([
    "New conversation",
    "View",
  ]);
  expect(within(cards[0]).getByText("The agent this workspace answers with by default.")).toBeTruthy();
  expect(within(cards[1]).getAllByRole("button").map((button) => button.textContent)).toEqual([
    "View",
  ]);
  expect(within(cards[1]).getByText("claude-opus-4-8")).toBeTruthy();
  expect(
    within(cards[2]).getByText("Spawned by an agent for one task, on that agent's model."),
  ).toBeTruthy();
  expect(within(cards[2]).getAllByRole("button").map((button) => button.textContent)).toEqual([
    "View",
  ]);
});

test("a member who is not an admin is offered no administration control", () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(screen.queryByRole("button", { name: "Administration" })).toBeNull();
});

test("the agent tab strip opens the tab named in the hash", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  wire({ "/skills": () => json({ skills: [] }), "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("No skill has been saved onto assistant yet.")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Skills" }).getAttribute("aria-selected")).toBe("true");
});

test("the model field offers the deploy's models, which its schema alone cannot supply", async () => {
  wire({
    "/overview": () => json(OVERVIEW),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect((await opened("model")).map((option) => option.textContent)).toEqual([
    "opus",
    "sonnet",
  ]);
});

test("a conversation the member may not read says so instead of reporting a status code", async () => {
  wire({
    "/turns": () => new Response("no", { status: 404 }),
    "/files": () => json({ files: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: "c1",
            title: "a shared thread",
            audience: "shared",
            updated_at: "2026-07-30T12:00:00",
            readable: true,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  expect(await screen.findByText("This conversation is not shared with you.")).toBeTruthy();
  expect(screen.queryByText(/Error 404/)).toBeNull();
});

test("a refusal after a consent link supersedes the link with the toned message", async () => {
  let calls = 0;
  wire({
    "/connections": () =>
      json({
        connections: [
          {
            provider: "github",
            account_id: "acct",
            owner_email: "member@example.com",
            shared: false,
            connected_at: "2026-07-01T00:00:00",
            grant: "g1",
          },
        ],
      }),
    "/intents": () => {
      calls += 1;
      return calls === 1
        ? json({ applied: true, message: "Queued.", turn_id: TURN_ID })
        : json({ applied: false, message: "The provider refuses it." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/connections";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
  await userEvent.type(
    await screen.findByLabelText("Provider"),
    "github",
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("connect", { url: "https://consent.example/go" });
  expect(
    await screen.findByRole("link", { name: "Open the provider consent page" }),
  ).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));
  await refusedNotice("The provider refuses it.");
  expect(screen.queryByRole("link", { name: "Open the provider consent page" })).toBeNull();
});

test("empty caps say so on both the workspace and the agent views", async () => {
  wire({
    "/workspace/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 0,
        by_dimension: [],
        caps: [],
        workspace: null,
      }),
  });
  const workspace = render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );
  expect(await screen.findByText("No spend cap is set on you.")).toBeTruthy();
  workspace.unmount();

  location.hash = "#/agents/" + AGENT.id + "/usage";
  wire({
    "/transcript": () => json({ messages: [] }),
    "/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 0,
        by_dimension: [],
        caps: [],
      }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("No spend cap is set on this agent.")).toBeTruthy();
});

test("an applied grant change keeps a live consent link on screen", async () => {
  let calls = 0;
  wire({
    "/connections": () =>
      json({
        connections: [
          {
            provider: "github",
            account_id: "acct",
            owner_email: "member@example.com",
            shared: false,
            connected_at: "2026-07-01T00:00:00",
            grant: "g1",
          },
        ],
      }),
    "/intents": () => {
      calls += 1;
      return calls === 1
        ? json({ applied: true, message: "Queued.", turn_id: TURN_ID })
        : json({ applied: true, message: "Shared." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/connections";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(await screen.findByRole("button", { name: "Add connection" }));
  await userEvent.type(
    await screen.findByLabelText("Provider"),
    "github",
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("connect", { url: "https://consent.example/go" });
  await screen.findByRole("link", { name: "Open the provider consent page" });

  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));
  await waitFor(() => expect(calls).toBe(2));
  expect(screen.getByRole("link", { name: "Open the provider consent page" })).toBeTruthy();
});
