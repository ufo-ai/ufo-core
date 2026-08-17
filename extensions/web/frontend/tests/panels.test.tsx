import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { REFUSAL_HEADER } from "@/lib/api";
import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  PlacedWorkspace,
  SECOND,
  SECOND_ID,
  NO_ARTIFACTS,
  NO_RUNS,
  SITE_KIND,
  StreamFake,
  TASK_KIND,
  TRIGGER_KIND,
  TURN_ID,
  fact,
  json,
  objectIndex,
  opened,
  owned,
  pick,
  refusedNotice,
  useStreamFake,
  pressRow,
  viewCard,
  wire,
} from "./harness";
beforeEach(() => {
  useStreamFake();
});

const IN_THREE_HOURS = new Date(Date.now() + 3 * 3_600_000).toISOString();

const SETTINGS = {
  agent: {
    name: "assistant",
    main: true,
    surfaces: ["web", "ufo"],
    updated_at: "2026-07-30T12:00:00",
    prompt: "be useful",
    prompt_digest: "abc123",
  },
  spec: { model: "opus", reasoning: "high", internet_access_allowed: true },
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

function usageDetails(totalMicroUsd: number, tokens: number = 1_200) {
  return {
    selected: {
      tokens,
      token_micro_usd: totalMicroUsd,
      total_micro_usd: totalMicroUsd,
    },
    all_time: {
      tokens: tokens * 4,
      token_micro_usd: totalMicroUsd * 4,
      total_micro_usd: totalMicroUsd * 4,
    },
    first_used_at: "2026-08-01T00:00:00+00:00",
    previous_tokens: tokens / 2,
    daily: [
      {
        day: "2026-08-12",
        tokens,
        token_micro_usd: totalMicroUsd,
        total_micro_usd: totalMicroUsd,
      },
    ],
    by_execution: [{ label: "", tokens, priced_micro_usd: totalMicroUsd }],
    by_model: [{ label: "claude-opus-4-8", tokens, priced_micro_usd: totalMicroUsd }],
  };
}

test("the settings page states the agent's facts, renders its schema, and submits a settings intent", async () => {
  const posted: unknown[] = [];
  wire({
    "/settings": () => json(SETTINGS),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(await screen.findByText("Main agent")).toBeTruthy();
  expect(fact("Installations")).toBe("Portal, Terminal");
  expect(fact("Updated")).toBe("Jul 30 2026");
  expect(fact("Prompt digest")).toBe("abc123");
  expect(fact("Web audience")).toBe("Every member");
  expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe("be useful");

  await pick("reasoning", "low");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { reasoning: "low", model: "opus", internet_access_allowed: true },
  });
  expect(await screen.findByText("Applied.")).toBeTruthy();
  expect((screen.getByLabelText("reasoning") as HTMLElement).textContent).toContain("low");

  await userEvent.clear(screen.getByLabelText("Prompt"));
  await userEvent.type(screen.getByLabelText("Prompt"), "review every request");
  await userEvent.click(screen.getByRole("button", { name: "Save prompt" }));
  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toEqual({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { prompt: "review every request" },
  });
  expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe(
    "review every request",
  );
});

test("switching agents discards unsaved settings edits", async () => {
  wire({
    "/settings": (url) =>
      json(
        url.includes(SECOND_ID)
          ? {
              ...SETTINGS,
              agent: { ...SETTINGS.agent, name: "second", prompt: "be second" },
            }
          : SETTINGS,
      ),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />,
  );
  await userEvent.clear(await screen.findByLabelText("Prompt"));
  await userEvent.type(screen.getByLabelText("Prompt"), "do not carry this");

  location.hash = "#/agents/" + SECOND_ID;
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  await waitFor(() =>
    expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe("be second"),
  );
});

test("settings polling preserves dirty edits", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  wire({
    "/settings": () => {
      reads += 1;
      return json(
        reads === 1
          ? SETTINGS
          : {
              ...SETTINGS,
              agent: {
                ...SETTINGS.agent,
                prompt: "changed elsewhere",
              },
              spec: { ...SETTINGS.spec, reasoning: "medium" },
            },
      );
    },
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: false, message: "Agent changed." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  vi.useFakeTimers();
  try {
    render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByDisplayValue("be useful")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("internet_access_allowed"));
    fireEvent.change(screen.getByLabelText("Prompt"), {
      target: { value: "review every request" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(reads).toBe(2);
    expect((screen.getByLabelText("internet_access_allowed") as HTMLInputElement).checked).toBe(
      false,
    );
    expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe(
      "review every request",
    );

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await act(async () => await Promise.resolve());
    fireEvent.click(screen.getByRole("button", { name: "Save prompt" }));
    await act(async () => await Promise.resolve());
    expect(posted).toMatchObject([
      {
        spec: { internet_access_allowed: false },
      },
      {
        spec: { prompt: "review every request" },
      },
    ]);
  } finally {
    vi.useRealTimers();
  }
});

test("a non-admin reads an agent prompt but cannot edit it", async () => {
  wire({
    "/settings": () => json({ ...SETTINGS, audience: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(await screen.findByText("be useful")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save prompt" })).toBeNull();
});

test("the agents index states what a row is, and leaves the address list to its own page", async () => {
  location.hash = "#/agents";
  render(
    <App
      agents={[
        { ...AGENT, web_audience: [] },
        { ...SECOND, web_audience: ["member@example.com"] },
        { ...SECOND, id: "33333333-3333-4333-8333-333333333333", name: "private", web_audience: [] },
      ]}
      subagents={[]}
      member={{ ...MEMBER, admin: true }}
      newAgent={null}
      onAgents={() => {}}
    />,
  );

  expect(await screen.findByRole("button", { name: MAIN_MARK })).toBeTruthy();
  expect(within(screen.getByRole("main")).queryByText("member@example.com")).toBeNull();
  expect(screen.queryByText("No member grants — admins only")).toBeNull();
});

test("non-admin agent rows keep their marks and no address list", () => {
  location.hash = "#/agents";
  render(
    <App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />,
  );

  expect(screen.getByRole("button", { name: MAIN_MARK })).toBeTruthy();
  expect(screen.queryByText("Every member")).toBeNull();
  expect(screen.queryByText("No member grants — admins only")).toBeNull();
});

test("a settings read that fails states the error and offers no form", async () => {
  wire({
    "/settings": () => new Response("nope", { status: 503 }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("the scheduled index leads with both runs, and its detail pauses through the intent lane", async () => {
  const posted: unknown[] = [];
  wire({
    "/objects/scheduled_task/digest": () =>
      json({
        ...TASK_KIND,
        name: "digest",
        summary: "0 9 * * * — summarize",
        spec: { schedule: "0 9 * * *", prompt: "summarize", paused: false },
        status: { next_run_at: IN_THREE_HOURS, paused: false },
        links: [],
        created_at: "2026-07-01T09:00:00Z",
        updated_at: "2026-07-01T09:00:00Z",
      }),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/objects/scheduled_task": () =>
      objectIndex(TASK_KIND, [
        owned({
          name: "digest",
          summary: "0 9 * * * — summarize",
          mine: true,
          next_run_at: IN_THREE_HOURS,
          last_run_at: "2026-07-31T09:00:00Z",
          last_run_status: "done",
          origin: "#general",
          prompt: "summarize",
          owner_email: "member@example.com",
          paused: false,
        }),
      ]),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Paused digest." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/radar?chip=scheduled_task";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const listed = (await screen.findByText("digest")).closest("tr");
  const said = [...(listed?.querySelectorAll("td") ?? [])].map((box) => String(box.textContent));
  expect(said[0]).toBe("digestActive");
  expect(said[1]).toBe("You");
  expect(said[2]).toBe("Jul 31 2026 · done");
  expect(said[3]).toContain("in ");
  expect(screen.queryByText("summarize")).toBeNull();
  expect(screen.queryByText("#general")).toBeNull();

  await userEvent.click(screen.getByText("digest"));
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
    "/workspace/artifacts": () => json({ artifacts: [] }),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/artifacts";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await viewCard("docs-abc"));

  expect(await screen.findByRole("heading", { name: "docs-abc" })).toBeTruthy();
  expect(document.querySelector('[data-part="refusal"]')).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

test("skills name where each came from, and save posts one skill file", async () => {
  const posted: unknown[] = [];
  wire({
    "/skills/community": () => json({ skills: [] }),
    "/skills": () =>
      json({
        skills: [
          { name: "mine", description: "member skill", origin: "member", instructions: "a" },
          { name: "shipped", description: "deploy skill", origin: "deploy", instructions: "b" },
        ],
      }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  const skills = within(await screen.findByTestId("panel"));
  expect(skills.queryByRole("tab", { name: "All" })).toBeNull();
  await userEvent.click(skills.getByRole("tab", { name: "Installed" }));
  expect(await screen.findByText("mine")).toBeTruthy();
  expect(screen.queryByText("member skill")).toBeNull();
  expect(skills.queryByRole("columnheader")).toBeNull();
  expect(screen.getByPlaceholderText("Search skills")).toBeTruthy();
  expect(skills.queryByRole("heading", { name: "Skills" })).toBeNull();
  expect(screen.getByText("mine").closest("li")?.textContent).toContain("Custom");
  expect(screen.getByText("shipped").closest("li")?.textContent).toContain("Built-in");
  expect(skills.queryByRole("button", { name: "Delete" })).toBeNull();

  await userEvent.click(skills.getByRole("button", { name: "New skill" }));
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

test("the skills tab opens on the directory and install files the fetched document", async () => {
  const posted: unknown[] = [];
  const DOCUMENT = '---\nname: release-notes\ndescription: "Drafts notes."\n---\n\nRead the tags.\n';
  const POPULAR = [
    { name: "release-notes", source: "acme/kit", installs: 12400 },
    { name: "mine", source: "acme/kit", installs: 900 },
  ];
  wire({
    "/skills/community/acme/kit/release-notes": () =>
      json({
        name: "release-notes",
        description: "Drafts notes.",
        instructions: "Read the tags.",
        document: DOCUMENT,
      }),
    "/skills/community?q=": (url) => {
      expect(url).toContain("q=release");
      return json({ skills: [POPULAR[0]] });
    },
    "/skills/community": () => json({ skills: POPULAR }),
    "/skills": () =>
      json({
        skills: [
          { name: "mine", description: "member skill", origin: "member", instructions: "a" },
        ],
      }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(await screen.findByText("release-notes")).toBeTruthy();
  expect(screen.getByText("acme/kit · 12.4K installs")).toBeTruthy();
  expect(document.querySelector("code")).toBeNull();
  const held = screen.getByText("mine").closest("li");
  const state = within(held as HTMLElement).getByRole("button", { name: "Installed" });
  expect((state as HTMLButtonElement).disabled).toBe(true);
  expect(within(held as HTMLElement).queryByRole("button", { name: "Install" })).toBeNull();
  const source = within(held as HTMLElement).getByRole("link", { name: "Source ↗" });
  expect(source.getAttribute("href")).toBe("https://github.com/acme/kit");
  expect(source.getAttribute("target")).toBe("_blank");
  expect(source.getAttribute("rel")).toBe("noopener noreferrer");

  await userEvent.type(screen.getByPlaceholderText("Search skills"), "release{Enter}");
  await waitFor(() => expect(screen.queryByText("mine")).toBeNull());

  await userEvent.click(screen.getByRole("button", { name: "Install" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("Description")).toHaveProperty("value", "Drafts notes.");
  expect(within(dialog).getByLabelText("Instructions")).toHaveProperty("value", "Read the tags.");

  await userEvent.click(within(dialog).getByRole("button", { name: "Install" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "skill",
    name: "release-notes",
    spec: { files: { "SKILL.md": DOCUMENT } },
  });
});

test("a directory read the member cannot correct is stated as a toast", async () => {
  const LIMITED = "The skill directory limits reads to 60 an hour and this deploy has reached it.";
  wire({
    "/skills/community/acme/kit/release-notes": () =>
      new Response(LIMITED, { status: 502, headers: { [REFUSAL_HEADER]: "1" } }),
    "/skills/community": () =>
      json({ skills: [{ name: "release-notes", source: "acme/kit", installs: 3 }] }),
    "/skills": () => json({ skills: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/skills";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Install" }));
  const toast = await screen.findByRole("status");
  expect(toast.textContent).toContain("release-notes did not open.");
  expect(toast.textContent).toContain("60 an hour");
  expect(screen.queryByRole("dialog", { name: "release-notes" })).toBeNull();
});

test("a private grant is shared with the agent from the connectors tab", async () => {
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
            own: true,
            grant: "g1",
            agents: [],
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
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(await screen.findByText("Only you")).toBeTruthy();
  await pressRow("github");
  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "apply", kind: "connector_grant", name: "g1" });
});

test("the agent's own tab lists what is shared with it and not what is held privately", async () => {
  wire({
    "/connections": () =>
      json({
        connections: [
          {
            provider: "github",
            account_id: "acct",
            owner_email: "member@example.com",
            shared: true,
            connected_at: "2026-07-01T00:00:00",
            own: true,
            grant: "g1",
            agents: [],
          },
          {
            provider: "notion",
            account_id: "acct2",
            owner_email: "member@example.com",
            shared: false,
            connected_at: "2026-07-01T00:00:00",
            own: true,
            grant: "g2",
            agents: [],
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByText("notion")).toBeTruthy();
});

test("a 404 usage read says it is not shared", async () => {
  wire({
    "/usage": () => new Response("no", { status: 404 }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/usage";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("Usage for this agent is not shared with you.")).toBeTruthy();
});

test("a workspace-shared conversation reads as shared, in its row and its detail heading", async () => {
  const shared = "5c0be3aa-0000-4000-8000-000000000003";
  wire({
    ["/conversations/" + shared + "/transcript"]: () => json({ messages: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: shared,
            surface: "slack",
            surface_label: null,
            audience: "shared",
            member_email: null,
            description: "",
            speakers: [],
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const row = await screen.findByRole("button", { name: /Slack · 3 turns/ });
  expect(row.querySelector("[data-part='primary']")!.textContent).toBe("Workspace");
  expect(row.querySelector("[data-part='meta']")!.textContent).toBe("Slack · 3 turns");
  expect(row.querySelector("[data-part='when']")!.textContent).toBe("Created Jul 30 2026");
  expect(row.textContent).not.toContain("slack");
  expect(row.textContent).not.toContain(shared.slice(0, 8));

  await userEvent.click(row);
  expect(await screen.findByText("Slack · Workspace")).toBeTruthy();
});

test("a conversation row names what it is about and whose it is, and the keyboard opens it", async () => {
  const opened = "8f2c1d40-0000-4000-8000-000000000004";
  wire({
    ["/conversations/" + opened + "/transcript"]: () => json({ messages: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: opened,
            surface: "slack",
            surface_label: null,
            audience: "shared",
            member_email: "mel@example.com",
            description: "can you take a look at the failing deploy",
            speakers: ["Mel Okafor (mel@example.com)", "pat@example.com"],
            turn_count: 4,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: "2026-08-07T11:00:00",
            readable: true,
            disclosable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const row = await screen.findByRole("button", {
    name: /can you take a look at the failing deploy/,
  });
  expect(row.getAttribute("tabindex")).toBe("0");
  expect(row.querySelector("[data-part='primary']")!.textContent).toBe(
    "can you take a look at the failing deploy",
  );
  expect(row.querySelector("[data-part='meta']")!.textContent).toBe(
    "mel@example.com · Slack · Mel Okafor, pat · 4 turns · Workspace",
  );
  expect(row.querySelector("[data-part='when']")!.textContent).toBe("Last turn Aug 7 2026");

  row.focus();
  expect(document.activeElement).toBe(row);
  await userEvent.keyboard("{Enter}");

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
  expect(
    screen.getByRole("heading", { name: "Slack · can you take a look at the failing deploy" }),
  ).toBeTruthy();
});

test("a Slack row names its channel on its meta line, and those words are the way out", async () => {
  const thread = "6b3f2a11-0000-4000-8000-000000000007";
  const portal = "2d9c4e88-0000-4000-8000-000000000008";
  const permalink = "https://acme.slack.com/archives/C1/p1700000000000100";
  wire({
    ["/conversations/" + thread + "/transcript"]: () => json({ messages: [] }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: thread,
            surface: "slack",
            surface_label: "#ops",
            audience: "shared",
            member_email: null,
            description: "take a look at the failing deploy",
            source: permalink,
            speakers: [],
            turn_count: 4,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: "2026-08-07T11:00:00",
            readable: true,
            disclosable: false,
          },
          {
            id: portal,
            surface: "web",
            surface_label: null,
            audience: "member:m1",
            member_email: "member@example.com",
            description: "Rename the deploy job",
            source: "ufo web (member@example.com)",
            speakers: ["member@example.com"],
            turn_count: 2,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: "2026-07-30T10:00:01",
            readable: true,
            disclosable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  const said = await screen.findByText("take a look at the failing deploy", {
    selector: "[data-part='primary']",
  });
  const row = said.closest("li") as HTMLElement;
  const meta = row.querySelector("[data-part='meta']") as HTMLElement;
  expect(meta.textContent).toBe("#ops ↗ · 4 turns · Workspace");
  const out = within(row).getByRole("link", { name: "#ops ↗" });
  expect(out.getAttribute("href")).toBe(permalink);
  expect(out.getAttribute("target")).toBe("_blank");
  expect(meta.contains(out)).toBe(true);
  expect(within(row).getAllByRole("link")).toHaveLength(1);
  const drawn = out.className.split(" ");
  expect(drawn).toContain("text-inherit");
  expect(drawn).toContain("no-underline");
  expect(drawn).toContain("hover:underline");
  expect(drawn).toContain("focus-visible:underline");
  expect(drawn).not.toContain("underline");
  expect(drawn).not.toContain("text-link");
  expect(within(out).getByText("↗").className).toContain("text-ink-soft");
  const own = screen.getByText("Rename the deploy job", { selector: "[data-part='primary']" });
  const mine = own.closest("li") as HTMLElement;
  expect(within(mine).queryByRole("link")).toBeNull();
  expect(mine.querySelector("[data-part='meta']")!.textContent).toBe(
    "You · Portal · member · 2 turns",
  );

  await userEvent.click(out);
  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();

  await userEvent.click(said);
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
});

test("a conversation opened here reads as chat, with the reply's whole activity behind it", async () => {
  const held = "7c4a1e90-0000-4000-8000-000000000005";
  const child = "9d2b7f31-0000-4000-8000-000000000006";
  wire({
    ["/conversations/" + held + "/transcript"]: () =>
      json({
        messages: [
          { role: "user", text: "parent ask" },
          {
            role: "assistant",
            text: "**parent answer**",
            events: [{ kind: "tool", name: "bash", preview: "ls", description: "" }],
            subagents: [
              {
                profile: "research",
                conversation_id: child,
                events: [{ kind: "note", text: "Reading the deploy job." }],
                output: "It runs nightly.",
                subagents: [],
              },
            ],
          },
        ],
      }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: held,
            surface: "web",
            surface_label: null,
            audience: "member:m1",
            member_email: "member@example.com",
            description: "Rename the deploy job",
            speakers: ["member@example.com"],
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: /Rename the deploy job/ }));

  expect(await screen.findByText("parent ask")).toBeTruthy();
  expect(screen.getByText("parent answer").tagName).toBe("STRONG");
  const summary = screen.getByText("Completed 2 steps");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("bash ls")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("bash ls")).toBeTruthy();
  const link = screen.getByRole("link", { name: /Subagent · research/ });
  expect(link.getAttribute("href")).toBe(
    "#/subagents/research/conversations/" + child + "?root=" + held,
  );

  await userEvent.click(link.closest("summary")!);
  expect(screen.getByText("Reading the deploy job.")).toBeTruthy();
  expect(screen.getByText("It runs nightly.")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Changes/ })).toBeNull();
});

test("a Slack transcript heads itself with its channel, and those words are the way back", async () => {
  const thread = "4f8e1c22-0000-4000-8000-000000000009";
  const portal = "5a7d3b44-0000-4000-8000-00000000000a";
  const root = "https://acme.slack.com/archives/C1/p1700000000000100";
  const rows = (id: string, surface: string, description: string) => ({
    id,
    surface,
    surface_label: null,
    audience: "shared",
    member_email: null,
    description,
    source: root,
    speakers: [],
    turn_count: 2,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: "2026-07-30T10:00:01",
    readable: true,
    disclosable: false,
  });
  wire({
    ["/conversations/" + thread + "/transcript"]: () =>
      json({
        messages: [
          { role: "user", text: "take a look at the failing deploy" },
          { role: "assistant", text: "Looking." },
          { role: "user", text: "and the flaky test" },
        ],
      }),
    ["/conversations/" + portal + "/transcript"]: () =>
      json({ messages: [{ role: "user", text: "Rename the deploy job" }] }),
    "/conversations": () =>
      json({
        conversations: [
          rows(thread, "slack", "take a look at the failing deploy"),
          rows(portal, "web", "Rename the deploy job"),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(
    await screen.findByText("take a look at the failing deploy", {
      selector: "[data-part='primary']",
    }),
  );

  const heading = await screen.findByRole("heading", {
    name: "Slack ↗ · take a look at the failing deploy",
  });
  const out = screen.getAllByRole("link", { name: "Slack ↗" });
  expect(out).toHaveLength(1);
  expect(out[0].getAttribute("href")).toBe(root);
  expect(out[0].getAttribute("target")).toBe("_blank");
  expect(out[0].getAttribute("rel")).toBe("noopener noreferrer");
  expect(heading.contains(out[0])).toBe(true);
  const drawn = out[0].className.split(" ");
  expect(drawn).toContain("text-inherit");
  expect(drawn).toContain("no-underline");
  expect(drawn).not.toContain("underline");
  expect(drawn).not.toContain("text-link");
  for (const bubble of document.querySelectorAll("[data-role=me]")) {
    expect(within(bubble as HTMLElement).queryByRole("link")).toBeNull();
  }

  await userEvent.click(screen.getByRole("button", { name: "All conversations" }));
  await userEvent.click(
    await screen.findByText("Rename the deploy job", { selector: "[data-part='primary']" }),
  );

  expect(await screen.findByText("Rename the deploy job")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Portal · Rename the deploy job" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: /↗/ })).toBeNull();
});

test("a conversation nobody shared offers no opener", async () => {
  wire({
    "/conversations": () =>
      json({
        conversations: [
          {
            id: "7ae41c02-0000-4000-8000-000000000001",
            surface: "slack",
            surface_label: "#ops",
            audience: "room:slack:C123",
            member_email: null,
            description: "",
            speakers: [],
            turn_count: 4,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: null,
            readable: false,
          },
          {
            id: "31bd9f77-0000-4000-8000-000000000002",
            surface: "slack",
            surface_label: null,
            audience: "room:slack:C456",
            member_email: null,
            description: "",
            speakers: [],
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
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  const named = await screen.findByText("#ops", { selector: "[data-part='primary']" });
  const unnamed = screen.getByText("Private channel", { selector: "[data-part='primary']" });
  expect(named.closest("li")!.querySelector("[data-part='meta']")!.textContent).toBe("4 turns");
  expect(unnamed.closest("li")!.querySelector("[data-part='meta']")!.textContent).toBe(
    "Slack · 2 turns",
  );
  expect(screen.queryAllByRole("button", { name: /channel/i })).toEqual([]);
  expect(named.closest("li")!.getAttribute("role")).toBeNull();
  expect(screen.queryByText(/Channel or room/)).toBeNull();
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
            own: true,
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
            own: true,
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
  const usageWire = wire({
    "/workspace/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 1_500_000,
        by_dimension: [{ dimension: "tokens", amount: 1200, priced_micro_usd: 1_500_000 }],
        caps: [{ window_seconds: 86_400, limit_micro_usd: 4_000, on_breach: "park" }],
        usage: usageDetails(1_500_000),
        workspace: {
          total_micro_usd: 9_000_000,
          by_dimension: [],
          by_member: [
            { label: "member@example.com", tokens: 7_200, priced_micro_usd: 9_000_000 },
          ],
          by_agent: [
            { id: AGENT_ID, label: "assistant", tokens: 7_200, priced_micro_usd: 9_000_000 },
          ],
          by_origin: [
            { label: "#eng", tokens: 6_000, priced_micro_usd: 7_500_000 },
            { label: "Workspace jobs", tokens: 1_200, priced_micro_usd: 1_500_000 },
          ],
          usage: usageDetails(9_000_000, 7_200),
        },
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("All-time tokens")).toBeTruthy();
  expect(screen.getAllByText("$9.00").length).toBeGreaterThan(1);
  expect(screen.getAllByText("7.2K").length).toBeGreaterThan(1);
  expect(screen.getByText("claude-opus-4-8")).toBeTruthy();
  expect(screen.getByText("<$0.01")).toBeTruthy();
  expect(screen.getByText("Suspend the turn")).toBeTruthy();
  expect(screen.getByText("member@example.com")).toBeTruthy();
  expect(screen.getByText("Origins")).toBeTruthy();
  expect(screen.getByText("#eng")).toBeTruthy();
  expect(screen.getByText("Workspace jobs")).toBeTruthy();
  expect(screen.getByRole("img", { name: "7.2K tokens across 1 daily buckets" })).toBeTruthy();
  expect(usageWire.calls.some((url) => url.includes("range=30d"))).toBe(true);

  await userEvent.click(screen.getByRole("button", { name: "90 days" }));
  await waitFor(() => expect(usageWire.calls.some((url) => url.includes("range=90d"))).toBe(true));
  expect(location.hash).toBe("#/workspace/usage?range=90d");
  expect(screen.getByRole("link", { name: "View" }).getAttribute("href")).toContain("range=90d");
});

test("a member with no rollup sees only their own figure and no workspace section", async () => {
  wire({
    "/workspace/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 1_500_000,
        by_dimension: [{ dimension: "egress", amount: 4, priced_micro_usd: 0 }],
        caps: [],
        usage: usageDetails(0, 0),
        workspace: null,
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("All-time tokens")).toBeTruthy();
  expect(screen.getByText("Sandbox requests")).toBeTruthy();
  expect(screen.queryByText("Agents")).toBeNull();
  expect(screen.queryByText("Members")).toBeNull();
});

test("the sidebar routes agents, sections, and the workspace by hash and marks the one selected", async () => {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/settings": () => json(SETTINGS),
    "/workspace/team": () => json({ members: [], can_add: false, domain: null }),
    "/workspace/radar": () => json({ runs: [] }),
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
    "/objects/source_trigger": () => objectIndex(TRIGGER_KIND, []),
    "/workspace/memory": () => json({ available: true, kinds: [], matches: [] }),
    "/skills": () => json({ skills: [] }),
    "/connections": () => json({ connections: [] }),
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [] }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  expect(location.hash).toBe("#/agents");
  expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-current")).toBe("true");

  await pressRow("second");
  expect(location.hash).toBe("#/agents/" + SECOND.id);
  expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-current")).toBe("true");

  await userEvent.click(screen.getByRole("button", { name: "Radar" }));
  expect(location.hash).toBe("#/radar");
  expect(await screen.findByText(NO_RUNS)).toBeTruthy();
  expect(screen.getAllByRole("tablist")).toHaveLength(1);
  expect(screen.getByRole("tab", { name: "Runs" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("button", { name: "Artifacts" }));
  expect(location.hash).toBe("#/artifacts");
  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Workspace" }));
  expect(location.hash).toBe("#/workspace/team");
  expect(screen.getByRole("button", { name: "Workspace" }).getAttribute("aria-current")).toBe(
    "true",
  );

  await userEvent.click(await screen.findByRole("tab", { name: "Memory" }));
  expect(location.hash).toBe("#/workspace/memory");
  expect(screen.getByRole("button", { name: "Workspace" }).getAttribute("aria-current")).toBe(
    "true",
  );
  expect(await screen.findByText("No memories yet.")).toBeTruthy();
});

/** The mark a subagent's name carries in the list, which stands in the cell beside the name. */
const SUBAGENT_MARK = "Subagent";
/** The same mark on the one agent this workspace answers with by default. */
const MAIN_MARK = "Main";

test("the agents view lists the deploy's subagents under the agents in one table", async () => {
  wire({
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(
    <App
      agents={[AGENT]}
      subagents={[
        { name: "deep_research", model: "claude-opus-4-8" },
        { name: "general_purpose", model: null },
      ]}
      member={MEMBER}
      newAgent={null}
      onAgents={() => {}}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Agents" }));
  const rows = [
    ...screen.getByRole("table").querySelectorAll<HTMLTableRowElement>("tbody tr"),
  ];
  expect(rows.map((row) => row.cells[0].textContent)).toEqual([
    "assistant" + MAIN_MARK,
    "deep_research" + SUBAGENT_MARK,
    "general_purpose" + SUBAGENT_MARK,
  ]);
  expect(screen.queryByText(/Spawned by an agent for one task/)).toBeNull();
  await waitFor(() =>
    expect(rows.map((row) => row.cells[1].textContent)).toEqual(["—", "—", "—"]),
  );
  expect(rows.map((row) => row.cells[2].textContent)).toEqual(["opus", "claude-opus-4-8", "—"]);
  expect(rows.every((row) => row.getAttribute("tabindex") === "0")).toBe(true);
});

test("a member who is not an admin is offered no administration control", () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect(screen.queryByRole("button", { name: "Administration" })).toBeNull();
});

test("the agent tab strip opens the tab named in the hash", async () => {
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  wire({
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("No account is connected to assistant yet.")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Connectors" }).getAttribute("aria-selected")).toBe(
    "true",
  );
});

test("the model field offers the deploy's models, which its schema alone cannot supply", async () => {
  wire({
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  expect((await opened("model")).map((option) => option.textContent)).toEqual([
    "opus",
    "sonnet",
  ]);
});

test("a conversation the member may not read says so instead of reporting a status code", async () => {
  wire({
    "/conversations/c1/transcript": () => new Response("no", { status: 404 }),
    "/conversations": () =>
      json({
        conversations: [
          {
            id: "c1",
            surface: "slack",
            surface_label: null,
            audience: "shared",
            member_email: null,
            description: "a shared thread",
            speakers: [],
            turn_count: 1,
            created_at: "2026-07-30T12:00:00",
            last_turn_at: "2026-07-30T12:00:00",
            readable: true,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: /a shared thread/ }));
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
            own: true,
            grant: "g1",
            agents: [],
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
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Add connector" }));
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

  await pressRow("github");
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
        usage: usageDetails(0, 0),
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
        usage: usageDetails(0, 0),
      }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
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
            own: true,
            grant: "g1",
            agents: [],
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
  location.hash = "#/agents/" + AGENT_ID + "/connectors";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
  await userEvent.click(await screen.findByRole("button", { name: "Add connector" }));
  await userEvent.type(
    await screen.findByLabelText("Provider"),
    "github",
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("connect", { url: "https://consent.example/go" });
  await screen.findByRole("link", { name: "Open the provider consent page" });

  await pressRow("github");
  await userEvent.click(screen.getByRole("button", { name: "Share with agent" }));
  await waitFor(() => expect(calls).toBe(2));
  expect(screen.getByRole("link", { name: "Open the provider consent page" })).toBeTruthy();
});

/** The bands a screen stacks carry no margin of their own, so whatever stacks them states the gap.
 *  A container that forgets it renders them flush — which is a fault no band can see, and which
 *  eight sections of Usage shipped past a green suite once already. */
test("every container that stacks bands states the one gap between them", async () => {
  wire({ "/workspace/team": () => json({ members: [], can_add: false }) });
  location.hash = "#/workspace/team";
  render(
    <App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />,
  );

  const panel = await screen.findByRole("tabpanel");
  expect(panel.className).toContain("gap-6xl");
  const page = panel.parentElement;
  expect(page?.className).toContain("gap-6xl");
});

test("an agent's conversation search asks the server rather than the rows on the page", async () => {
  const kept = "5c0be3aa-0000-4000-8000-000000000009";
  const row = (id: string, description: string) => ({
    id,
    surface: "web",
    surface_label: null,
    audience: "shared",
    member_email: null,
    description,
    speakers: [],
    turn_count: 1,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: null,
    readable: true,
    disclosable: false,
  });
  const { calls } = wire({
    "/conversations?q=deploy": () => json({ conversations: [row(kept, "Rename the deploy job")] }),
    "/conversations": () =>
      json({
        conversations: [
          row(kept, "Rename the deploy job"),
          row("5c0be3aa-0000-4000-8000-000000000010", "Order more coffee"),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "/conversations";
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);

  expect(await screen.findByText("Order more coffee")).toBeTruthy();
  await userEvent.type(await screen.findByLabelText("Search"), "deploy{Enter}");

  expect(await screen.findByText("Rename the deploy job")).toBeTruthy();
  expect(screen.queryByText("Order more coffee")).toBeNull();
  expect(calls.some((url) => url.includes("/conversations?q=deploy"))).toBe(true);
});
