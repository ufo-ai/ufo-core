import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import type { ChatRow } from "@/lib/rail";
import { readRail } from "@/lib/railStore";
import { newChatHash } from "@/lib/route";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  audienceMark,
  chatsOnWire,
  conversationObject,
  json,
  useStreamFake,
  wire,
} from "./harness";

const WORKSPACE_ROW = { ...CHAT_ROW, audience: "shared", member_email: null };
const MADE_PRIVATE =
  "Only you read this conversation from now on. Notes made here are yours by default.";
const SHARED_WITH_WORKSPACE =
  "Every member of the workspace can read this conversation, including its past messages. " +
  "Notes made here are the workspace's by default.";

const action = (name: string) => ({
  name,
  description: "",
  input_schema: { properties: {} },
  call: { kind: "conversation", action: name, name: CONVO_ID, input: {} },
  label: name,
});

const ACTIONS = [action("make_conversation_private"), action("share_conversation")];

const chat = (row: ChatRow) => ({
  ...chatsOnWire([row]),
  "/transcript": () => json({ messages: [] }),
  "/slots": () => json({ slots: [] }),
  ["/actions/conversation/" + CONVO_ID + "$"]: () => json({ actions: ACTIONS }),
});

function open() {
  return render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

test("a workspace chat of the member's draws the visibility control on the title line, a private one the lock", async () => {
  wire(chat(WORKSPACE_ROW));
  const shared = open();

  const control = await screen.findByRole("button", { name: "Visibility: Workspace" });
  expect(document.body.querySelector("[data-slot=header]")!.contains(control)).toBe(true);
  expect(control.getAttribute("title")).toContain("Every member of the workspace reads");
  expect(document.body.querySelector("[data-slot=audience]")).toBeNull();
  shared.unmount();

  wire(chat(CHAT_ROW));
  open();

  const own = await screen.findByRole("button", { name: "Visibility: Private" });
  expect(own.getAttribute("title")).toContain("Only you read this conversation.");
});

test("another member's chat states its audience and offers no act", async () => {
  wire(chat({ ...CHAT_ROW, audience: "member:m2", member_email: "mel@example.com", mine: false }));
  open();

  await screen.findByText("No messages in this conversation yet.");
  expect(audienceMark().getAttribute("title")).toContain("Only mel@example.com reads");
  expect(screen.queryByRole("button", { name: /^Visibility: / })).toBeNull();
});

test("a new conversation states Private before its first answer finishes", async () => {
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
  open();

  await userEvent.type(await screen.findByLabelText("Ask UFO"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByRole("button", { name: "Visibility: Private" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Visibility: Workspace" })).toBeNull();
});

test("choosing Private toggles in the menu immediately and dispatches the prepared intent", async () => {
  const posted: { url: string; body: unknown }[] = [];
  let serverRow: ChatRow = WORKSPACE_ROW;
  let finish: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  wire({
    ...chat(WORKSPACE_ROW),
    "/objects/conversation$": () => json({ objects: [conversationObject(serverRow)] }),
    "/make_conversation_private": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return pending;
    },
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Visibility: Workspace" }));
  expect(
    (await screen.findByRole("menuitemradio", { name: "Workspace" })).getAttribute(
      "aria-checked",
    ),
  ).toBe("true");
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Private" }));

  expect(screen.getByRole("button", { name: "Visibility: Private" })).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: "Make private" })).toBeNull();
  expect(posted).toHaveLength(1);
  expect(posted[0].url).toBe(
    "/surface/web/agents/" +
      AGENT.id +
      "/actions/conversation/" +
      CONVO_ID +
      "/make_conversation_private",
  );
  expect(posted[0].body).toEqual({});

  readRail();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Visibility: Private" })).toBeTruthy(),
  );

  serverRow = { ...CHAT_ROW, member_email: MEMBER.email };
  finish(json({ applied: true, message: MADE_PRIVATE }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Visibility: Private" })
        .getAttribute("aria-disabled"),
    ).toBeNull(),
  );
});

test("choosing Workspace toggles immediately and dispatches share_conversation", async () => {
  const posted: string[] = [];
  let serverRow: ChatRow = CHAT_ROW;
  let finish: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  wire({
    ...chat(CHAT_ROW),
    "/objects/conversation$": () => json({ objects: [conversationObject(serverRow)] }),
    "/share_conversation": (url) => {
      posted.push(url);
      return pending;
    },
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Visibility: Private" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: "Workspace" }));

  expect(screen.getByRole("button", { name: "Visibility: Workspace" })).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(posted).toHaveLength(1);
  expect(posted[0]).toBe(
    "/surface/web/agents/" +
      AGENT.id +
      "/actions/conversation/" +
      CONVO_ID +
      "/share_conversation",
  );

  serverRow = WORKSPACE_ROW;
  finish(json({ applied: true, message: SHARED_WITH_WORKSPACE }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Visibility: Workspace" })
        .getAttribute("aria-disabled"),
    ).toBeNull(),
  );
});

test("a refused optimistic change restores the server state and states the refusal", async () => {
  let finish: (response: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  wire({
    ...chat(WORKSPACE_ROW),
    "/make_conversation_private": () => pending,
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Visibility: Workspace" }));
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Private" }));
  expect(screen.getByRole("button", { name: "Visibility: Private" })).toBeTruthy();

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
