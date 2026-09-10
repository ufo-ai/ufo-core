import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Suspense, lazy } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { PaneFault } from "@/kernel/pane";
import { Loading } from "@/kernel/panel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ADMIN_DISCLOSURE } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";
import { agentHash } from "@/lib/route";
import { openAgent, placeHome } from "@/lib/router";
import { agentCrumb } from "@/lib/title";
import { ConversationSlotPane } from "@/views/ConversationSlotPane";

import { AGENT, AGENT_ID, agentIndex, atPhoneWidth, audienceMark, CHAT_ROW, chatsOnWire, CONVO_ID, destination, fact, FRESH, heldConversation, json, MEMBER, openAgentSettings, openConversation, opened, pick, PlacedWorkspace, pressRow, refusedNotice, SECOND, SECOND_ID, SETTINGS, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";
beforeEach(() => {
  useStreamFake();
});

async function openWorkspaceColumn(): Promise<HTMLElement> {
  await userEvent.click(await screen.findByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  return within(drawer).getByRole("navigation", { name: "Workspace" });
}

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
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();
  expect(await screen.findByText("Main app")).toBeTruthy();
  expect(fact("Installations")).toBe("Portal, Terminal");
  expect(fact("Updated")).toBe("Jul 30 2026");
  expect(fact("Prompt digest")).toBe("abc123");
  expect(fact("Web audience")).toBe("Every member");
  expect(fact("Usage")).toBe("Workspace usage");
  expect(screen.queryByRole("tab", { name: "Usage" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Add connector" })).toBeNull();
  expect(screen.getByText("be useful")).toBeTruthy();

  await pick("reasoning", "Low");

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { reasoning: "low", model: "opus", internet_access_allowed: true },
  });
  expect(await screen.findByText("Assistant saved.")).toBeTruthy();
  expect((screen.getByLabelText("reasoning") as HTMLElement).textContent).toContain("Low");

  await userEvent.click(screen.getByRole("button", { name: "Edit prompt" }));
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
  expect(await screen.findByRole("button", { name: "Edit prompt" })).toBeTruthy();
  expect(screen.queryByLabelText("Prompt")).toBeNull();
});

/** Two whole-spec intents sent at once can land either way round, and the older landing last would undo
 *  the newer choice — so they are sent one after the other. */
test("a second preference changed mid-save is the one that lands last", async () => {
  const posted: { spec: Record<string, unknown> }[] = [];
  // Held on an object rather than in a local: a local assigned only inside the executor narrows to never.
  const held: { release?: () => void } = {};
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/intents": async (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      if (posted.length === 1) {
        await new Promise<void>((resume) => {
          held.release = resume;
        });
      }
      return json({ applied: true, message: "Applied." });
    },
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();

  await userEvent.click(await screen.findByLabelText("internet_access_allowed"));
  await waitFor(() => expect(posted.length).toBe(1));

  await userEvent.click(screen.getByLabelText("Use workspace skills"));
  expect(posted.length).toBe(1);

  held.release?.();
  await waitFor(() => expect(posted.length).toBe(2));

  expect(posted[1].spec).toMatchObject({
    internet_access_allowed: false,
    use_workspace_skills: false,
  });
});

test("a refusal names itself and puts the controls back to what is stored", async () => {
  const posted: unknown[] = [];
  const held: { release?: () => void } = {};
  let reads = 0;
  wire({
    "/settings": () => {
      reads += 1;
      return json(SETTINGS);
    },
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/intents": async (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      if (posted.length === 1) {
        await new Promise<void>((resume) => {
          held.release = resume;
        });
        return json({ applied: false, message: "reasoning is required by that model." });
      }
      return json({ applied: true, message: "Applied." });
    },
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();
  const before = reads;

  await userEvent.click(await screen.findByLabelText("internet_access_allowed"));
  await waitFor(() => expect(posted.length).toBe(1));

  const skills = screen.getByLabelText("Use workspace skills") as HTMLInputElement;
  await userEvent.click(skills);
  expect(skills.checked).toBe(false);

  held.release?.();

  expect(await screen.findByText("reasoning is required by that model.")).toBeTruthy();
  await waitFor(() => expect(reads).toBeGreaterThan(before));
  expect(posted.length).toBe(1);
  await waitFor(() =>
    expect((screen.getByLabelText("Use workspace skills") as HTMLInputElement).checked).toBe(true),
  );
});

test("the settings usage fact opens the workspace usage tab", async () => {
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
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
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();

  await userEvent.click(await screen.findByRole("link", { name: "Workspace usage" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/usage"));
  expect(destination()).toBe("Usage");
  expect(await screen.findByText("No spend cap is set on you.")).toBeTruthy();
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
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(
    <App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />,
  );
  await openAgentSettings();
  await userEvent.click(await screen.findByRole("button", { name: "Edit prompt" }));
  await userEvent.clear(await screen.findByLabelText("Prompt"));
  await userEvent.type(screen.getByLabelText("Prompt"), "do not carry this");

  location.hash = "#/agents/" + SECOND_ID;
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  await waitFor(() =>
    expect(screen.getByText("be second")).toBeTruthy(),
  );
});

test("settings polling preserves a dirty prompt edit", async () => {
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
    "/connections": () => json({ connections: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: false, message: "Agent changed." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
    await openAgentSettings();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("be useful")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Edit prompt" }));
    fireEvent.change(screen.getByLabelText("Prompt"), {
      target: { value: "review every request" },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(reads).toBe(2);
    expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).value).toBe(
      "review every request",
    );

    fireEvent.click(screen.getByRole("button", { name: "Save prompt" }));
    await act(async () => await Promise.resolve());
    expect(posted).toMatchObject([{ spec: { prompt: "review every request" } }]);
  } finally {
    vi.useRealTimers();
  }
});

test("a non-admin reads an agent prompt but cannot edit it", async () => {
  wire({
    "/settings": () => json({ ...SETTINGS, audience: null }),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();
  expect(await screen.findByText("be useful")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save prompt" })).toBeNull();
});

test("the agents index states a row's name alone, and leaves the address list to its own page", async () => {
  atPhoneWidth();
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents";
  render(
    <App
      agents={[
        { ...AGENT, web_audience: [] },
        { ...SECOND, web_audience: ["member@example.com"] },
        { ...SECOND, id: "33333333-3333-4333-8333-333333333333", name: "private", web_audience: [] },
      ]}
      member={{ ...MEMBER, admin: true }}
      onAgents={() => {}}
    />,
  );

  const page = within(await screen.findByRole("main"));
  await openWorkspaceColumn();
  const index = within(await agentIndex());
  expect(index.getByText("Assistant")).toBeTruthy();
  expect(index.getByText("Second")).toBeTruthy();
  expect(index.getByText("Private")).toBeTruthy();
  expect(index.queryByText("opus")).toBeNull();
  expect(page.queryByText("member@example.com")).toBeNull();
  expect(screen.queryByText("No member grants — admins only")).toBeNull();
});

test("a non-admin reads the same index rows", async () => {
  atPhoneWidth();
  wire({ "/transcript": () => json({ messages: [] }) });
  location.hash = "#/agents";
  render(
    <App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />,
  );

  await openWorkspaceColumn();
  const index = within(await agentIndex());
  expect(index.getByText("Assistant")).toBeTruthy();
  expect(index.getByText("Second")).toBeTruthy();
  expect(screen.queryByText("Every member")).toBeNull();
  expect(screen.queryByText("No member grants — admins only")).toBeNull();
});

test("a settings read that fails states the error and offers no form", async () => {
  wire({
    "/settings": () => new Response("nope", { status: 503 }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();
  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});



test("a private grant is shared with the agent from the settings connectors section", async () => {
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
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");
  expect(await screen.findByText("Only you")).toBeTruthy();
  await pressRow("github");
  await userEvent.click(screen.getByRole("button", { name: "Share with app" }));
  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({ verb: "apply", kind: "connection", name: "g1" });
});

test("the agent's own section lists what is shared with it and not what is held privately", async () => {
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
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByText("notion")).toBeTruthy();
});

test("a workspace-shared conversation names its audience and surface in its band", async () => {
  const shared = "5c0be3aa-0000-4000-8000-000000000003";
  wire({
    ["/conversations/" + shared + "/transcript"]: () => json({ messages: [] }),
    "/conversations$": () =>
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
            commentable: true,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "?open=" + shared;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const band = await heldConversation();
  expect(band).toBe("Slack · Workspace");
  expect(band).not.toContain(shared.slice(0, 8));
});

test("a conversation the address names is opened, and named by what it is about", async () => {
  const opened = "8f2c1d40-0000-4000-8000-000000000004";
  const said = "can you take a look at the failing deploy";
  wire({
    ["/conversations/" + opened + "/transcript"]: () => json({ messages: [] }),
    "/conversations$": () =>
      json({
        conversations: [
          {
            id: opened,
            surface: "slack",
            surface_label: null,
            audience: "shared",
            member_email: "mel@example.com",
            description: said,
            speakers: ["Mel Okafor (mel@example.com)", "pat@example.com"],
            turn_count: 4,
            created_at: "2026-07-30T10:00:00",
            last_turn_at: "2026-08-07T11:00:00",
            readable: true,
            disclosable: false,
            commentable: true,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await heldConversation()).toBe(FRESH);

  openConversation(AGENT_ID, opened);

  await waitFor(async () => expect(await heldConversation()).toBe("Slack · " + said));
  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();
});

test("a Slack conversation names its channel in its heading, and those words are the way out", async () => {
  const thread = "6b3f2a11-0000-4000-8000-000000000007";
  const permalink = "https://acme.slack.com/archives/C1/p1700000000000100";
  wire({
    ["/conversations/" + thread + "/transcript"]: () => json({ messages: [] }),
    "/conversations$": () =>
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
            commentable: true,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "?open=" + thread;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const heading = await screen.findByRole("heading", {
    name: "#ops ↗ · take a look at the failing deploy",
  });
  const out = within(heading).getByRole("link", { name: "#ops ↗" });
  expect(out.getAttribute("href")).toBe(permalink);
  expect(out.getAttribute("target")).toBe("_blank");
  expect(within(heading).getAllByRole("link")).toHaveLength(1);
  const drawn = out.className.split(" ");
  expect(drawn).toContain("text-inherit");
  expect(drawn).toContain("no-underline");
  expect(drawn).toContain("hover:underline");
  expect(drawn).toContain("focus-visible:underline");
  expect(drawn).not.toContain("underline");
  expect(drawn).not.toContain("text-link");
  expect(within(out).getByText("↗").className).toContain("text-ink-soft");
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
  expect(screen.queryByText(/read-only here/)).toBeNull();
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
            events: [{ kind: "activity", text: "Listing the workspace." }],
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
    "/conversations$": () =>
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
            commentable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "?open=" + held;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("parent ask")).toBeTruthy();
  expect(screen.getByText("parent answer").tagName).toBe("STRONG");
  const summary = screen.getByText("Completed 2 steps");
  expect(summary.closest("summary")).toBeTruthy();
  expect(screen.queryByText("bash ls")).toBeNull();

  await userEvent.click(summary);
  expect(screen.getByText("Listing the workspace.")).toBeTruthy();
  const run = screen.getByText("Subagent · research");
  expect(run.closest("a")).toBeNull();

  await userEvent.click(run.closest("summary")!);
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
    commentable: surface === "slack",
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
    "/conversations$": () =>
      json({
        conversations: [
          rows(thread, "slack", "take a look at the failing deploy"),
          rows(portal, "web", "Rename the deploy job"),
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "?open=" + thread;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const heading = await screen.findByRole("heading", {
    name: "Slack ↗ · take a look at the failing deploy",
  });
  const out = within(heading).getAllByRole("link", { name: "Slack ↗" });
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

  openConversation(AGENT_ID, portal);

  const portalHeading = await screen.findByRole("heading", {
    name: "Portal · Rename the deploy job",
  });
  expect(within(portalHeading).queryByRole("link")).toBeNull();
});

test("a conversation nobody shared is never named, and the half stands on the composer", async () => {
  wire({
    "/conversations$": () =>
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
            commentable: false,
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
            commentable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await heldConversation()).toBe(FRESH);
  expect(screen.queryByText("#ops")).toBeNull();
  expect(screen.queryByText("Private channel")).toBeNull();
  expect(screen.getByLabelText("Ask UFO")).toBeTruthy();
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
            { id: null, label: "Workspace jobs", tokens: 0, priced_micro_usd: 0 },
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
  expect(screen.getByText("Assistant")).toBeTruthy();
  expect(screen.getAllByText("Workspace jobs").length).toBe(2);
  expect(screen.getByRole("img", { name: "$9.00 across 1 daily buckets" })).toBeTruthy();
  expect(usageWire.calls.some((url) => url.includes("range=30d"))).toBe(true);

  await userEvent.click(screen.getByRole("button", { name: "90 days" }));
  await waitFor(() => expect(usageWire.calls.some((url) => url.includes("range=90d"))).toBe(true));
  expect(screen.getByRole("button", { name: "90 days" }).getAttribute("aria-pressed")).toBe("true");
});

test("the usage range offers the last 24 hours, and the day history is the kit's bar plot", async () => {
  const usageWire = wire({
    "/workspace/usage": () =>
      json({
        window_seconds: 86400,
        total_micro_usd: 1_500_000,
        by_dimension: [{ dimension: "tokens", amount: 1200, priced_micro_usd: 1_500_000 }],
        caps: [],
        usage: usageDetails(1_500_000),
        workspace: null,
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );

  const plot = await screen.findByRole("img", { name: "$1.50 across 1 daily buckets" });
  expect(plot.getAttribute("data-slot")).toBe("chart");

  await userEvent.click(screen.getByRole("button", { name: "24 hours" }));
  await waitFor(() => expect(usageWire.calls.some((url) => url.includes("range=1d"))).toBe(true));
  expect(screen.getByRole("button", { name: "24 hours" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("Last 24 hours")).toBeTruthy();
});

test("the daily average is an average over the chosen period, not over the dates it touches", async () => {
  const spread = {
    ...usageDetails(1_500_000),
    daily: [
      { day: "2026-08-12", tokens: 600, token_micro_usd: 750_000, total_micro_usd: 750_000 },
      { day: "2026-08-13", tokens: 600, token_micro_usd: 750_000, total_micro_usd: 750_000 },
    ],
  };
  const usageWire = wire({
    "/workspace/usage": (url) =>
      json({
        window_seconds: url.includes("range=1d") ? 86_400 : 2_592_000,
        total_micro_usd: 1_500_000,
        by_dimension: [{ dimension: "tokens", amount: 1200, priced_micro_usd: 1_500_000 }],
        caps: [],
        usage: spread,
        workspace: null,
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );

  const average = (value: string, note: string) =>
    waitFor(() => {
      const tile = screen.getByText("Daily average").parentElement!;
      expect(within(tile).getByText(value)).toBeTruthy();
      expect(within(tile).getByText(note)).toBeTruthy();
    });

  await average("40", "2 active days");

  await userEvent.click(screen.getByRole("button", { name: "24 hours" }));
  await waitFor(() => expect(usageWire.calls.some((url) => url.includes("range=1d"))).toBe(true));
  await average("1.2K", "1 active day");
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
  expect(screen.queryByRole("heading", { name: "Apps" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "Members" })).toBeNull();
});

test("a member who is not an admin is offered no administration control", async () => {
  atPhoneWidth();
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const foot = within(await openWorkspaceColumn());
  expect(foot.getByRole("button", { name: "Theme" })).toBeTruthy();
  expect(foot.queryByRole("button", { name: "Administration" })).toBeNull();
});

test("the drawer rides the address the home screen replaces, and shuts when the member moves", async () => {
  atPhoneWidth();
  wire({ "/transcript": () => json({ messages: [] }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openWorkspaceColumn();

  act(() => placeHome({ opens: [AGENT_ID] }, "replace"));
  expect(screen.getByRole("dialog")).toBeTruthy();

  act(() => openAgent(AGENT_ID));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("an app opens on the conversation that moved last, and an address names another", async () => {
  const older = "44444444-4444-4444-8444-444444444444";
  const listed = (id: string, description: string) => ({
    id,
    surface: "web",
    surface_label: null,
    audience: "member:m1",
    member_email: "member@example.com",
    description,
    speakers: ["member@example.com"],
    turn_count: 1,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: "2026-07-30T10:00:01",
    readable: true,
    disclosable: false,
    commentable: false,
  });
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    ...chatsOnWire([
      { ...CHAT_ROW, title: "Newest thread" },
      { ...CHAT_ROW, conversation_id: older, title: "Older thread" },
    ]),
    "/conversations$": () =>
      json({
        conversations: [listed(CONVO_ID, "Newest thread"), listed(older, "Older thread")],
      }),
    "/homepage": () => json({ state: "none" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await heldConversation()).toBe("Newest thread");
  expect(location.hash).toBe("#/agents/" + AGENT_ID);
  expect(screen.queryByRole("tab", { name: "Home" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Conversations" })).toBeNull();

  openConversation(AGENT_ID, older);

  await waitFor(async () => expect(await heldConversation()).toBe("Older thread"));
  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=" + older);
});

test("an app with no page opens its newest conversation whole, with no lane beside", async () => {
  const older = "44444444-4444-4444-8444-444444444444";
  const listed = (id: string, description: string, readable = true) => ({
    id,
    agent: null,
    surface: "web",
    surface_label: null,
    audience: "member:m1",
    member_email: "member@example.com",
    description,
    source: null,
    speakers: ["member@example.com"],
    turn_count: 1,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: "2026-07-30T10:00:01",
    readable,
    disclosable: false,
  });
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    ...chatsOnWire([
      { ...CHAT_ROW, title: "Newest thread" },
      { ...CHAT_ROW, conversation_id: older, title: "Older thread" },
    ]),
    "/conversations$": () =>
      json({
        conversations: [listed(CONVO_ID, "Newest thread"), listed(older, "Older thread")],
      }),
    "/homepage": () => json({ state: "none" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await waitFor(async () => expect(await heldConversation()).toBe("Newest thread"));
  expect(screen.queryByRole("region", { name: "Conversations" })).toBeNull();
});

test("the app pane starts a conversation where it stands, without leaving for the chat screen", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    ...chatsOnWire([CHAT_ROW]),
    "/homepage": () => json({ state: "none" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await screen.findByRole("region", { name: "Assistant" });
  const act = within(pane).getByRole("button", { name: "New" });
  const menu = within(pane).getByRole("button", { name: "Menu for Assistant" });
  expect(menu.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();

  await userEvent.click(act);

  await waitFor(async () => expect(await heldConversation()).toBe(FRESH));
  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=new");
});

test("New starts a fresh conversation while the address still names one", async () => {
  const held = "44444444-4444-4444-8444-444444444444";
  location.hash = "#/agents/" + AGENT_ID + "?open=" + held;
  wire({
    ...chatsOnWire([{ ...CHAT_ROW, conversation_id: held }]),
    "/conversations$": () =>
      json({
        conversations: [
          {
            id: held,
            agent: null,
            surface: "web",
            surface_label: null,
            audience: "shared",
            member_email: MEMBER.email,
            description: "The one already open",
            source: null,
            speakers: [MEMBER.email],
            turn_count: 2,
            created_at: "2026-08-01T09:00:00",
            last_turn_at: "2026-08-07T11:00:00",
            readable: true,
            disclosable: false,
            commentable: false,
          },
        ],
      }),
    "/homepage": () => json({ state: "none" }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const pane = await screen.findByRole("region", { name: "Assistant" });
  await waitFor(async () => expect(await heldConversation()).toBe("The one already open"));

  await userEvent.click(within(pane).getByRole("button", { name: "New" }));

  await waitFor(async () => expect(await heldConversation()).toBe(FRESH));
  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=new");
});

test("the model field offers the deploy's models, which its schema alone cannot supply", async () => {
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();
  expect((await opened("model")).map((option) => option.textContent)).toEqual([
    "Opus",
    "Sonnet",
  ]);
});

test("a conversation the member may not read says so instead of reporting a status code", async () => {
  wire({
    "/conversations/c1/transcript": () => new Response("no", { status: 404 }),
    "/conversations$": () =>
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
            commentable: false,
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID + "?open=c1";
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
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
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");
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
  await userEvent.click(screen.getByRole("button", { name: "Share with app" }));
  await refusedNotice("The provider refuses it.");
  expect(screen.queryByRole("link", { name: "Open the provider consent page" })).toBeNull();
});

test("empty caps say so on the workspace view", async () => {
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
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );
  expect(await screen.findByText("No spend cap is set on you.")).toBeTruthy();
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
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  location.hash = "#/agents/" + AGENT_ID;
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");
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
  await userEvent.click(screen.getByRole("button", { name: "Share with app" }));
  await waitFor(() => expect(calls).toBe(2));
  expect(screen.getByRole("link", { name: "Open the provider consent page" })).toBeTruthy();
});

/** The bands a screen stacks carry no margin of their own, so whatever stacks them states the gap. A
 *  container that forgets renders them flush, which is a fault no band can see. */
test("every container that stacks bands states the one gap between them", async () => {
  wire({ "/workspace/team": () => json({ members: [], can_manage: false }) });
  location.hash = "#/workspace/team";
  render(
    <App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />,
  );

  const panel = await screen.findByTestId("workspace");
  expect(panel.className).toContain("gap-6xl");
  const column = panel.parentElement;
  expect(column?.className).toContain("gap-6xl");
  expect(column?.className).toContain("max-w-page");
  expect(column?.parentElement?.className).toContain("gap-6xl");
});

test("a slot standing in a lane names nothing of its own", async () => {
  wire({
    ["/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({ type: "artifacts", artifacts: [], truncated: false }),
  });
  render(
    <ConversationSlotPane
      agent={AGENT}
      conversationId={CONVO_ID}
      slot="artifacts"
      summary={{ id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifact", count: 0 }}
      embedded
    />,
  );

  expect(await screen.findByText("No artifacts shared in this conversation.")).toBeTruthy();
  expect(screen.queryByRole("complementary")).toBeNull();
  expect(screen.queryByLabelText("Artifacts")).toBeNull();
  expect(document.body.querySelector("[data-slot=audience]")).toBeNull();
});

test("a slot standing on its own address is headed by its name under the app", async () => {
  wire({
    ["/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({ type: "artifacts", artifacts: [], truncated: false }),
  });
  render(
    <ConversationSlotPane
      agent={AGENT}
      conversationId={CONVO_ID}
      slot="artifacts"
      summary={{ id: "artifacts", label: "Artifacts", icon: "artifact", kind: "artifact", count: 0 }}
      crumb={agentCrumb(AGENT)}
    />,
  );

  const head = await screen.findByRole("heading", { level: 1, name: "Artifacts" });
  const path = screen.getByRole("navigation", { name: "Breadcrumb" });
  expect(path.contains(head)).toBe(true);
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(within(path).getByRole("link", { name: "Back to Assistant" }).getAttribute("href")).toBe(
    agentHash(AGENT.id),
  );
});

test("a slot on its own address states who reads the conversation it belongs to", async () => {
  wire({
    ["/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({ type: "artifacts", artifacts: [], truncated: false }),
  });
  render(
    <TooltipProvider>
      <ConversationSlotPane
        agent={AGENT}
        conversationId={CONVO_ID}
        slot="artifacts"
        summary={{
          id: "artifacts",
          label: "Artifacts",
          icon: "artifact",
          kind: "artifact",
          count: 0,
        }}
        audience={{ audience: "member:m1", member_email: null, surface_label: null }}
        crumb={agentCrumb(AGENT)}
      />
    </TooltipProvider>,
  );

  const head = await screen.findByRole("heading", { level: 1, name: "Artifacts" });
  const detail = "Only you read this conversation. " + ADMIN_DISCLOSURE;
  const mark = audienceMark();
  expect(mark.parentElement!.contains(head)).toBe(true);
  expect(mark.textContent).toBe(detail);
  expect(mark.getAttribute("title")).toBe(detail);
  fireEvent.focus(mark);
  expect((await screen.findByRole("tooltip")).textContent).toBe("Only you");
});

const APP_SITE = { state: "set", url: "https://tasks.example.test", deploy_generation: 1 } as const;

function appConversation(id: string, description: string, surface = "web") {
  return {
    id,
    agent: null,
    surface,
    surface_label: null,
    audience: "member:m1",
    member_email: "member@example.com",
    description,
    source: null,
    speakers: ["member@example.com"],
    turn_count: 1,
    created_at: "2026-07-30T10:00:00",
    last_turn_at: "2026-07-30T10:00:01",
    readable: true,
    disclosable: false,
    commentable: false,
  };
}

function appOnWire(conversations: ReturnType<typeof appConversation>[], more = false) {
  return {
    ...chatsOnWire([{ ...CHAT_ROW, title: "Newest thread" }]),
    "/conversations$": () => json({ conversations, more }),
    "/homepage": () => json(APP_SITE),
    "/setup": () => json({ own_page: false, connectors: [], credentials: [], standing: [] }),
    "/transcript": () => json({ messages: [] }),
  };
}

const APP = { ...AGENT, app: "tasks", homepage: APP_SITE };
const OLDER = "44444444-4444-4444-8444-444444444444";

test("the lane's History lists the app's conversations, and one press opens it in the lane", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=" + CONVO_ID;
  wire(
    appOnWire([
      appConversation(CONVO_ID, "Newest thread"),
      appConversation(OLDER, "Older thread", "slack"),
    ]),
  );
  render(<App agents={[APP]} member={MEMBER} onAgents={() => {}} />);

  const act = await screen.findByRole("button", { name: "History for Assistant" });
  const start = screen.getByRole("button", { name: "New conversation with Assistant" });
  expect(start.compareDocumentPosition(act) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

  await userEvent.click(act);

  expect(act.getAttribute("aria-pressed")).toBe("true");
  const row = await screen.findByRole("button", { name: /^Newest thread \d+(mo|y)$/ });
  const other = screen.getByRole("button", { name: /^Older thread Slack \d+(mo|y)$/ });
  const line = within(row).getByText("Newest thread");
  const stamp = row.querySelector("time")!;
  expect(stamp.getAttribute("title")).toBe("Jul 30 2026 at 10:00 GMT+5:30");
  expect(line.contains(stamp)).toBe(false);
  expect(line.compareDocumentPosition(stamp) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

  await userEvent.click(other);

  await waitFor(() => expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=" + OLDER));
  expect(
    screen.queryByRole("button", { name: "History for Assistant" })?.getAttribute("aria-pressed"),
  ).toBe("false");
});

test("the list's way back stands beside its name, and leaves it without shutting the lane", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=" + CONVO_ID;
  wire(appOnWire([appConversation(CONVO_ID, "Newest thread")]));
  render(<App agents={[APP]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  await screen.findByRole("button", { name: /^Newest thread \d+(mo|y)$/ });

  const back = screen.getByRole("button", { name: "Back" });
  const name = within(screen.getByRole("region", { name: "History" })).getByText("History");
  expect(back.compareDocumentPosition(name) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Close History" })).toBeNull();

  await userEvent.click(back);

  expect(screen.queryByRole("button", { name: /^Newest thread \d+(mo|y)$/ })).toBeNull();
  expect(location.hash).toBe("#/agents/" + AGENT_ID + "?open=" + CONVO_ID);
  expect(await screen.findByRole("button", { name: "Close Newest thread" })).toBeTruthy();
});

test("an app nobody has spoken to says so rather than standing an empty list", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=new";
  wire(appOnWire([]));
  render(<App agents={[APP]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));

  expect(await screen.findByText("No conversations yet.")).toBeTruthy();
});

test("a history longer than the read carries says so under the rows", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=" + CONVO_ID;
  wire(appOnWire([appConversation(CONVO_ID, "Newest thread")], true));
  render(<App agents={[APP]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));

  expect(await screen.findByText("The newest few. Search finds an older one.")).toBeTruthy();
});

test("a history the read carries whole ends on its last row", async () => {
  location.hash = "#/agents/" + AGENT_ID + "?open=" + CONVO_ID;
  wire(appOnWire([appConversation(CONVO_ID, "Newest thread")]));
  render(<App agents={[APP]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "History for Assistant" }));
  await screen.findByRole("button", { name: /^Newest thread \d+(mo|y)$/ });

  expect(screen.queryByText(/The newest few/)).toBeNull();
});

function Standing({ at }: { at: string }) {
  return (
    <PaneFault at={at}>
      <Suspense fallback={<Loading />}>{at === "unreached" ? <Unreached /> : <p>Arrived.</p>}</Suspense>
    </PaneFault>
  );
}

const Unreached = lazy(() => Promise.reject(new Error("chunk")));

test("a pane whose code never arrives says so rather than blanking the screen", async () => {
  render(<Standing at="unreached" />);

  expect(await screen.findByText("This page did not load — reload to retry.")).toBeTruthy();
});

test("the pane the member steps to next draws, rather than the fault the last one left", async () => {
  const view = render(<Standing at="unreached" />);
  await screen.findByText("This page did not load — reload to retry.");

  view.rerender(<Standing at="arrived" />);

  expect(await screen.findByText("Arrived.")).toBeTruthy();
});
