import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import type { ChatRow } from "@/lib/rail";
import { newChatHash } from "@/lib/route";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  chatsOnWire,
  json,
  useStreamFake,
  wire,
} from "./harness";

const WORKSPACE_ROW: ChatRow = { ...CHAT_ROW, audience: "shared", member_email: null };
const PRIVATE_ACTION = {
  name: "make_conversation_private",
  description: "",
  input_schema: { properties: {} },
  call: {
    kind: "conversation",
    action: "make_conversation_private",
    name: CONVO_ID,
    input: {},
  },
  label: "Make private",
};

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

test("the sidebar visibility menu updates immediately and rolls a refused change back", async () => {
  let finish: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  wire({
    ...chatsOnWire([WORKSPACE_ROW]),
    "/transcript": () => json({ messages: [] }),
    "/slots": () => json({ slots: [] }),
    ["/actions/conversation/" + CONVO_ID + "$"]: () => json({ actions: [PRIVATE_ACTION] }),
    "/make_conversation_private": () => pending,
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const trigger = await screen.findByRole("button", { name: "Visibility: Workspace" });
  const icon = trigger.querySelector("svg");
  expect(icon).not.toBeNull();
  expect(icon?.closest("button")).toBe(trigger);
  await userEvent.click(icon!);
  expect(await screen.findByRole("menuitemradio", { name: "Workspace" })).toBeTruthy();
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Private" }));

  expect(screen.getByRole("button", { name: "Visibility: Private" })).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();

  finish(
    json({
      applied: false,
      message: "Another member has spoken in this conversation, so it stays with the workspace.",
    }),
  );

  expect(
    await screen.findByText(
      "Another member has spoken in this conversation, so it stays with the workspace.",
    ),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Visibility: Workspace" })).toBeTruthy();
});

test("a new sidebar conversation states Private before its first answer finishes", async () => {
  wire({
    ...chatsOnWire([]),
    "/chat": () =>
      json({
        turn_id: "33333333-3333-4333-8333-333333333333",
        conversation_id: CONVO_ID,
        title: "hello",
      }),
    "/transcript": () => json({ messages: [] }),
    "/slots": () => json({ slots: [] }),
  });
  location.hash = newChatHash(AGENT.id);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByRole("button", { name: "Visibility: Private" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Visibility: Workspace" })).toBeNull();
});
