import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, AGENT_ID, atPhoneWidth, CHAT_APP, CHAT_APP_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, SECOND, useStreamFake, wire } from "../../tests/harness";

beforeEach(() => {
  location.hash = "#/";
  useStreamFake();
});

const transcript = () => ({
  ...chatsOnWire([CHAT_ROW]),
  "/transcript": () => json({ messages: [] }),
  "/slots": () =>
    json({
      slots: [
        { id: "changes", label: "Changes", icon: "diff", kind: "changes", count: 0 },
      ],
    }),
});

test("a thread pressed in the rail lands the cursor in the composer of the pane it opens", async () => {
  const owned = { ...CHAT_ROW, agent_id: CHAT_APP_ID, agent_name: "chat" };
  wire({ ...transcript(), ...chatsOnWire([owned]) });
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: /Pick one thread/ }));

  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  const box = await screen.findByLabelText("Ask UFO");
  await waitFor(() => expect(document.activeElement).toBe(box));
});

test("a thread pressed in the rail at a phone width leaves the composer alone", async () => {
  atPhoneWidth();
  const owned = { ...CHAT_ROW, agent_id: CHAT_APP_ID, agent_name: "chat" };
  wire({ ...transcript(), ...chatsOnWire([owned]) });
  render(<App agents={[AGENT, CHAT_APP]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(screen.getByRole("button", { name: "Menu" }));
  const drawer = await screen.findByRole("dialog");
  await userEvent.click(await within(drawer).findByRole("button", { name: /Pick one thread/ }));

  await waitFor(() => expect(location.hash).toBe("#/c/" + CONVO_ID));
  const box = await screen.findByLabelText("Ask UFO");
  expect(document.activeElement).not.toBe(box);
});

test("a route renaming the start screen keeps the same box, and the place in its words", async () => {
  wire({ ...transcript() });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const box = (await screen.findByLabelText("Ask UFO")) as HTMLTextAreaElement;
  await userEvent.type(box, "draft this");
  box.setSelectionRange(6, 6);

  await userEvent.click(screen.getAllByRole("button", { name: "New chat" })[0]);

  expect(location.hash).toBe("#/new/" + AGENT_ID);
  const after = screen.getByLabelText("Ask UFO") as HTMLTextAreaElement;
  expect(after).toBe(box);
  expect(after.value).toBe("draft this");
  expect(after.selectionStart).toBe(6);
  expect(after.selectionEnd).toBe(6);
});
