import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import type { FirstRunPayload } from "@/lib/firstRun";
import { BUILD_STEP_MS } from "@/views/FirstRun";
import { resetChatStore } from "@/lib/chatStore";
import { chatHash } from "@/lib/route";

import { AGENT, chatsOnWire, CONVO_ID, json, MEMBER, type Route, TURN_ID, useStreamFake, wire } from "../../tests/harness";

const ADMIN = { ...MEMBER, admin: true };

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Setting up" };

const RECORD_VIEW: FirstRunPayload["actions"]["memory"][number] = {
  name: "record_first_run",
  description: "Record what the first run learned.",
  call: { kind: "memory", action: "record_first_run", input: {} },
  input_schema: { properties: { body: { type: "string", maxLength: 400 } }, required: ["body"] },
  label: "Continue",
};

const FIRST_RUN = {
  providers: [{ name: "github", label: "GitHub", summary: "Read and write code.", group: "Code" }],
  mcp_servers: [],
  connectors: [],
  model_key_held: true,
  workspace_domain: null,
  actions: { member: [], memory: [RECORD_VIEW], enrichment_profile: [] },
};

let sent: string[] = [];

beforeEach(() => {
  location.hash = "";
  history.replaceState(null, "", location.pathname + "?first=1");
  sessionStorage.clear();
  resetChatStore();
  sent = [];
  useStreamFake();
});

function mount(routes: Record<string, Route> = {}) {
  wire({
    ...chatsOnWire([]),
    "/workspace/first-run": () => json(FIRST_RUN),
    "/actions/": () => json({ applied: true, message: "Saved." }),
    "/chat": (_url, init) => {
      sent.push(String(init?.body));
      return json(OPENED);
    },
    "/transcript": () => json({ messages: [] }),
    ...routes,
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);
}

test("the run draws on this shell, and its thread is the chat the member lands on", async () => {
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Get started" }));
  await screen.findByRole("heading", { name: "What’s the website for your business?" });
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.type(await screen.findByLabelText("About your business"), "Design studio");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(sent).toEqual([
      "I just set up this workspace. My business: Design studio. " +
        "Set up my first task: a daily competitive analysis.",
    ]),
  );
  const founder = await screen.findByRole("button", { name: "Founder" });
  expect(founder.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "Which tools do you work in?" });
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await userEvent.click(screen.getByRole("button", { name: "Next" }));

  await userEvent.click(
    await screen.findByRole("button", { name: "Open your workspace" }, { timeout: BUILD_STEP_MS * 5 }),
  );

  await waitFor(() => expect(location.hash).toBe(chatHash(CONVO_ID)));
});
