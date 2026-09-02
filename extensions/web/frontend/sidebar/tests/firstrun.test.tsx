/** The sidebar half of the one first run. The screens, the memory write and the thread per goal
 *  are the shared component's and are proved once in the lanes suite (`tests/firstrun.test.tsx`);
 *  what only this shell can be wrong about is where the handoff lands, so that is what this file
 *  holds. It also proves the shared module resolves against this shell's own tree — a `@/…` import
 *  that reached the other shell's kernel would draw the run with the wrong panel and API. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { BUILD_STEP_MS, type FirstRunPayload } from "@/views/FirstRun";
import { resetChatStore } from "@/lib/chatStore";

import { AGENT, chatsOnWire, CONVO_ID, json, MEMBER, type Route, TURN_ID, useStreamFake, wire } from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const OPENED = { turn_id: TURN_ID, conversation_id: CONVO_ID, title: "Setting up" };

/** The memory act the run writes through, as the read projects it for this member. */
const RECORD_VIEW: FirstRunPayload["actions"]["memory"][number] = {
  name: "record_first_run",
  description: "Record what the first run learned.",
  call: { kind: "memory", action: "record_first_run", input: {} },
  input_schema: { properties: { body: { type: "string", maxLength: 400 } }, required: ["body"] },
  label: "Continue",
};

/** A deploy with no Slack install and no enrichment: the run is the three questions, which is the
 *  shortest path to the handoff this file is about. */
const FIRST_RUN = {
  providers: [{ name: "github", label: "GitHub" }],
  connectors: [{ name: "github", label: "GitHub", installed: false }],
  imessage: false,
  model_key_held: true,
  actions: { member: [], memory: [RECORD_VIEW], enrichment_profile: [] },
};

let sent: string[] = [];

beforeEach(() => {
  location.hash = "";
  history.replaceState(null, "", location.pathname + "?first=1");
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

test("the run draws on this shell, and its handoff lands in the agent's own chat", async () => {
  mount();

  // The shared component's own first screen, drawn through this shell's panel kernel.
  await userEvent.click(await screen.findByRole("button", { name: "Get started" }));
  await userEvent.type(await screen.findByLabelText("About your business"), "Design studio");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Founder" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await userEvent.click(screen.getByRole("button", { name: "Next" }));

  // The build screen is the shared component's too, and the way out of it is this shell's.
  await userEvent.click(
    await screen.findByRole("button", { name: "Open your workspace" }, { timeout: BUILD_STEP_MS * 5 }),
  );

  const box = await screen.findByLabelText("Ask UFO");
  await waitFor(() =>
    expect(sent.at(-1)).toBe(
      "I just set up this workspace. My business: Design studio. My role: Founder. " +
        "Set up my first task: a daily competitive analysis.",
    ),
  );
  expect((box as HTMLTextAreaElement).value).toBe("");
});

test("a picked goal opens its own thread before the handoff, on this shell too", async () => {
  mount();

  await userEvent.click(await screen.findByRole("button", { name: "Get started" }));
  await userEvent.type(await screen.findByLabelText("About your business"), "Design studio");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(await screen.findByRole("radio", { name: "Founder" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "What is top of mind right now?" });
  await userEvent.click(screen.getByRole("button", { name: "Growing revenue" }));
  await userEvent.click(screen.getByRole("button", { name: "Next" }));

  await screen.findByRole("heading", { name: "Creating your business’s workspace" });
  await waitFor(() => expect(sent.length).toBe(1));
  expect(sent[0]).toContain("My goal: growing revenue.");
  expect(sent[0]).toContain("Ask me at most one thing.");
});
