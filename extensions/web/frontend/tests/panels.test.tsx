import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";
import { Workspace } from "@/views/Workspace";

import { AGENT, AGENT_ID, MEMBER, SECOND, json, useStreamFake, wire } from "./harness";

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
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "overview" }));
  expect(await screen.findByText("main agent · installations: web · updated 2026-07-30 12:00")).toBeTruthy();
  expect(screen.getByText("digest abc123 — prompt changes go through the governed proposal path in chat")).toBeTruthy();
  expect(screen.getByText("be useful")).toBeTruthy();
  expect(screen.getByText("every member")).toBeTruthy();

  await userEvent.selectOptions(screen.getByLabelText("reasoning"), "low");
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
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "overview" }));
  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("tasks list their state and pause through the intent lane", async () => {
  const posted: unknown[] = [];
  const tasks = {
    tasks: [
      {
        name: "digest",
        schedule: "0 9 * * *",
        prompt: "summarize",
        description: null,
        created_by: "member@example.com",
        paused: false,
        next_run_at: "2026-08-01T09:00:00",
        last_run_at: null,
        expires_at: null,
      },
    ],
    spec_schema: { properties: { schedule: { type: "string" }, prompt: { type: "string" } } },
  };
  wire({
    "/tasks": () => json(tasks),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Paused digest." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "tasks" }));
  expect(await screen.findByText("scheduled")).toBeTruthy();
  expect(screen.getByText("2026-08-01 09:00")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Pause" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "scheduled_task",
    name: "digest",
    spec: { paused: true },
  });
});

test("a task with a hidden prompt offers only its cadence fields", async () => {
  wire({
    "/tasks": () =>
      json({
        tasks: [
          {
            name: "private",
            schedule: "0 9 * * *",
            prompt: null,
            description: null,
            created_by: "other@example.com",
            paused: false,
            next_run_at: null,
            last_run_at: null,
            expires_at: null,
          },
        ],
        spec_schema: {
          properties: {
            schedule: { type: "string" },
            prompt: { type: "string" },
            expires_at: { type: "string" },
          },
        },
      }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "tasks" }));
  expect(await screen.findByText("private member task")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));

  expect(screen.getByLabelText("schedule")).toBeTruthy();
  expect(screen.getByLabelText("expires_at")).toBeTruthy();
  expect(screen.queryByLabelText("prompt")).toBeNull();
});

test("skills separate member-authored from deploy, and save posts one skill file", async () => {
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
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "skills" }));
  expect(await screen.findByText("member skill")).toBeTruthy();
  expect(screen.getByText("deploy skill")).toBeTruthy();

  await userEvent.type(screen.getByPlaceholderText("skill-name"), "fresh");
  await userEvent.type(screen.getByPlaceholderText(/^---/), "body");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "skill",
    name: "fresh",
    spec: { files: { "SKILL.md": "body" } },
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
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "connections" }));
  expect(await screen.findByText("private")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "apply", kind: "connector_grant", name: "g1" });

  await userEvent.click(screen.getByRole("tab", { name: "usage" }));
  expect(await screen.findByText("Usage for this agent is not shared with you.")).toBeTruthy();
});

test("conversations open a turn tree that nests a subagent under the turn that spawned it", async () => {
  wire({
    "/conversations/c1/turns": () =>
      json({
        turns: [
          {
            id: "t1",
            seq: 1,
            status: "done",
            created_at: "2026-07-30T10:00:00",
            inbound: "parent ask",
            outcome: "parent answer",
            error_class: null,
            subagent_profile: null,
            parent_turn_id: null,
          },
        ],
        subagent_turns: [
          {
            id: "t2",
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
    "/conversations/c1/files": () => json({ files: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: "c1",
            surface: "web",
            queue_key: "q",
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
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "conversations" }));
  await userEvent.click(await screen.findByRole("button", { name: "Open" }));

  expect(await screen.findByText("parent ask")).toBeTruthy();
  expect(screen.getByText("child answer")).toBeTruthy();
  const parent = screen.getByText(/^turn 1 ·/);
  const child = screen.getByText(/^subagent research ·/);
  expect(parent.compareDocumentPosition(child) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(await screen.findByText("No files in this conversation's workspace.")).toBeTruthy();
});

test("a conversation nobody shared offers no opener", async () => {
  wire({
    "/conversations": () =>
      json({
        conversations: [
          {
            id: "c2",
            surface: "slack",
            queue_key: "room",
            member_email: null,
            turn_count: 4,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: null,
            readable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "conversations" }));
  expect(await screen.findByText("not shared with you")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
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
      <Workspace view="sources" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("databases, pages")).toBeTruthy();
  expect(screen.getByText("3")).toBeTruthy();
  expect(screen.getByText("2026-08-01 06:00")).toBeTruthy();

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
        by_dimension: [{ dimension: "model", amount: 1200, priced_micro_usd: 1_500_000 }],
        caps: [],
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
      <Workspace view="usage" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("Your spend · last 24h · $1.500000")).toBeTruthy();
  expect(screen.getByText("1,200")).toBeTruthy();
  expect(screen.getByText("No caps are set on you.")).toBeTruthy();
  expect(screen.getByText("Workspace · $9.000000")).toBeTruthy();
  expect(screen.getByText("member@example.com")).toBeTruthy();
});

test("the workspace sidebar routes by hash and the agent list marks the one selected", async () => {
  wire({ "/transcript": () => json({ messages: [] }), "/workspace/sites": () => json({ available: false, sites: [] }) });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);

  const second = screen.getByRole("button", { name: /second/ });
  await userEvent.click(second);
  expect(location.hash).toBe("#/agents/" + SECOND.id);
  expect(second.getAttribute("aria-current")).toBe("true");
  expect(screen.getByRole("button", { name: /assistant/ }).getAttribute("aria-current")).toBe("false");

  await userEvent.click(screen.getByRole("button", { name: "Sites" }));
  expect(location.hash).toBe("#/workspace/sites");
  expect(await screen.findByText("No sites extension is installed.")).toBeTruthy();
});

test("a member who is not an admin is offered no administration control", () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} member={MEMBER} />);
  expect(screen.queryByRole("button", { name: "Administration" })).toBeNull();
});

test("the agent tab strip opens the tab named in the hash", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  wire({ "/skills": () => json({ skills: [] }), "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} member={MEMBER} />);

  expect(await screen.findByText("No member-authored skills for assistant.")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "skills" }).getAttribute("aria-selected")).toBe("true");
});

test("the model field offers the deploy's models, which its schema alone cannot supply", async () => {
  wire({
    "/overview": () => json(OVERVIEW),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "overview" }));
  const model = (await screen.findByLabelText("model")) as HTMLSelectElement;
  expect(model.tagName).toBe("SELECT");
  expect([...model.options].map((option) => option.value)).toEqual(["opus", "sonnet"]);
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
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.click(screen.getByRole("tab", { name: "conversations" }));
  await userEvent.click(await screen.findByRole("button", { name: "Open" }));
  expect(await screen.findByText("This conversation is not shared with you.")).toBeTruthy();
  expect(screen.queryByText(/Error 404/)).toBeNull();
});
