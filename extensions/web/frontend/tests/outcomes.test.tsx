import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { refusedNotice, AGENT, MEMBER, StreamFake, TURN_ID, json, useStreamFake, wire } from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const SLOT = {
  name: "openai",
  slot: "OPENAI_API_KEY",
  extension: "models",
  description: "the key",
  filled: true,
};

beforeEach(() => {
  useStreamFake();
});

test("a connect intent opens the stream for the turn it reports and shows the consent link", async () => {
  location.hash = "#/agents/" + AGENT.id + "/connections";
  wire({
    "/connections": () => json({ connections: [] }),
    "/intents": () => json({ applied: true, message: "Requested.", turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.type(
    await screen.findByPlaceholderText("Provider (github, notion, …)"),
    "github",
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toContain("/turns/" + TURN_ID + "/stream");

  StreamFake.last().emit("connect", { url: "https://consent.example/authorize" });
  const link = await screen.findByRole("link", { name: "Open the provider consent page" });
  expect(link.getAttribute("href")).toBe("https://consent.example/authorize");
});

test("a credential intent that answers with a request renders the prompt carrying its seal", async () => {
  location.hash = "#/workspace/credentials";
  const posts: string[] = [];
  const intents: string[] = [];
  wire({
    "/workspace/credentials": () => json({ slots: [SLOT] }),
    "/intents": (_url, init) => {
      intents.push(String(init?.body));
      return json({
        applied: true,
        message: "",
        turn_id: TURN_ID,
        credentials: {
          sealed: "seal-token",
          reason: "models authenticates with this value.",
          prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
        },
      });
    },
    "/credentials": (_url, init) => {
      posts.push(String(init?.body));
      return json({ stored: true });
    },
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  expect(await screen.findByText("models authenticates with this value.")).toBeTruthy();

  await userEvent.type(await screen.findByPlaceholderText("OPENAI_API_KEY"), "sk-live");
  await userEvent.click(screen.getByRole("button", { name: "Store" }));

  await waitFor(() => expect(posts.length).toBe(1));
  expect(JSON.parse(intents[0])).toEqual({
    verb: "request",
    kind: "credential",
    name: "openai",
  });
  const sent = new URLSearchParams(posts[0]);
  expect(sent.get("sealed")).toBe("seal-token");
  expect(sent.get("slot")).toBe("OPENAI_API_KEY");
  expect(sent.get("value")).toBe("sk-live");
});

test("a stored credential states the slot it stored and re-reads the listing", async () => {
  location.hash = "#/workspace/credentials";
  let reads = 0;
  wire({
    "/workspace/credentials": () => {
      reads += 1;
      return json({ slots: [SLOT] });
    },
    "/intents": () =>
      json({
        applied: true,
        message: "",
        turn_id: TURN_ID,
        credentials: {
          reason: "models authenticates with this value.",
          sealed: "seal-token",
          prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
        },
      }),
    "/credentials": () => json({ stored: true }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  await userEvent.type(await screen.findByPlaceholderText("OPENAI_API_KEY"), "sk-live");
  await userEvent.click(screen.getByRole("button", { name: "Store" }));

  expect(await screen.findByText("Stored OPENAI_API_KEY.")).toBeTruthy();
  await waitFor(() => expect(reads).toBe(2));
});

test("a cleared credential states the outcome and re-reads the listing", async () => {
  location.hash = "#/workspace/credentials";
  let reads = 0;
  wire({
    "/workspace/credentials": () => {
      reads += 1;
      return json({ slots: [SLOT] });
    },
    "/intents": () => json({ applied: true, message: "Cleared OPENAI_API_KEY." }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Clear" }));

  expect(await screen.findByText("Cleared OPENAI_API_KEY.")).toBeTruthy();
  await waitFor(() => expect(reads).toBe(2));
});

test("two acts in a row each re-read, even though the server answers one constant message", async () => {
  location.hash = "#/workspace/credentials";
  let reads = 0;
  wire({
    "/workspace/credentials": () => {
      reads += 1;
      return json({ slots: [SLOT, { ...SLOT, name: "notion", slot: "NOTION_TOKEN" }] });
    },
    "/intents": () => json({ applied: true, message: "Saved.", turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  const clears = await screen.findAllByRole("button", { name: "Clear" });
  await userEvent.click(clears[0]);
  expect(await screen.findByText("Saved.")).toBeTruthy();
  await waitFor(() => expect(reads).toBe(2));

  const again = await screen.findAllByRole("button", { name: "Clear" });
  await userEvent.click(again[1]);
  await waitFor(() => expect(reads).toBe(3));
});

const REQUESTED = {
  applied: true,
  message: "",
  turn_id: TURN_ID,
  credentials: {
    reason: "models authenticates with this value.",
    sealed: "seal-token",
    prompts: [{ slot: "OPENAI_API_KEY", prompt: "the key" }],
  },
};

test("an empty slot offers Set, and a refused act states the refusal in place", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () => json({ slots: [{ ...SLOT, filled: false }] }),
    "/intents": () => json({ applied: false, message: "Only an admin may set it." }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Set" }));

  await refusedNotice("Only an admin may set it.");
  expect(screen.queryByRole("button", { name: "Clear" })).toBe(null);
});

test("a workspace with no declared slots says so", async () => {
  location.hash = "#/workspace/credentials";
  wire({ "/workspace/credentials": () => json({ slots: [] }) });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  expect(await screen.findByText("No credential slots are declared.")).toBeTruthy();
});

test("the secret field hides what a member types and refuses whitespace", async () => {
  location.hash = "#/workspace/credentials";
  const posts: string[] = [];
  wire({
    "/workspace/credentials": () => json({ slots: [SLOT] }),
    "/intents": () => json(REQUESTED),
    "/credentials": (_url, init) => {
      posts.push(String(init?.body));
      return json({ stored: true });
    },
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  const field = await screen.findByPlaceholderText("OPENAI_API_KEY");
  expect(field.getAttribute("type")).toBe("password");

  await userEvent.type(field, "   ");
  await userEvent.click(screen.getByRole("button", { name: "Store" }));
  expect(posts.length).toBe(0);
});

test("a refused store states the reason the server gave and keeps the field", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () => json({ slots: [SLOT] }),
    "/intents": () => json(REQUESTED),
    "/credentials": () => new Response("that seal has expired", { status: 400 }),
  });
  render(<App agents={[AGENT]} subagents={[]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Replace" }));
  await userEvent.type(await screen.findByPlaceholderText("OPENAI_API_KEY"), "sk-live");
  await userEvent.click(screen.getByRole("button", { name: "Store" }));

  expect(await screen.findByText("the key — that seal has expired.")).toBeTruthy();
  expect(screen.getByPlaceholderText("OPENAI_API_KEY")).toBeTruthy();
});

test("the tasks, overview, and skills refusals tone their notices", async () => {
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
      properties: { model: { type: "string" }, reasoning: { type: "string", enum: ["low", "high"] } },
    },
    models: ["opus"],
    deploy: { sandbox_internet: true },
    audience: [],
  };
  const TASKS = {
    tasks: [
      {
        name: "digest",
        schedule: "0 9 * * *",
        prompt: "summarize",
        description: null,
        created_by: "member@example.com",
        paused: false,
        next_run_at: null,
        last_run_at: null,
        expires_at: null,
      },
    ],
    spec_schema: { properties: { schedule: { type: "string" } } },
  };
  const refuse = () => json({ applied: false, message: "The workspace refuses it." });

  location.hash = "#/agents/" + AGENT.id + "/tasks";
  wire({ "/tasks": () => json(TASKS), "/transcript": () => json({ messages: [] }), "/intents": refuse });
  const first = render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
  await refusedNotice("The workspace refuses it.");
  first.unmount();

  location.hash = "#/agents/" + AGENT.id + "/overview";
  wire({ "/overview": () => json(OVERVIEW), "/transcript": () => json({ messages: [] }), "/intents": refuse });
  const second = render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.click(await screen.findByRole("button", { name: "Save" }));
  await refusedNotice("The workspace refuses it.");
  second.unmount();

  location.hash = "#/agents/" + AGENT.id + "/skills";
  wire({ "/skills": () => json({ skills: [] }), "/transcript": () => json({ messages: [] }), "/intents": refuse });
  const third = render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  await userEvent.type(await screen.findByPlaceholderText("skill-name"), "triage");
  await userEvent.type(screen.getByPlaceholderText(/name: skill-name/), "steps");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await refusedNotice("The workspace refuses it.");
  third.unmount();
});
