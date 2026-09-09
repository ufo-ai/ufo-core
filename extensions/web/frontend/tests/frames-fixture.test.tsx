import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import rows from "./fixtures/sse.json";
import {
  AGENT,
  CONVO_ID,
  MEMBER,
  StreamFake,
  TURN_ID,
  json,
  useStreamFake,
  wire,
} from "./harness";

type Row = { event: string; data: string };
const fixture = rows as Row[];

function payload<T>(event: string): T {
  const row = fixture.find((candidate) => candidate.event === event);
  if (!row) throw new Error(`no fixture row for ${event}`);
  return JSON.parse(row.data) as T;
}

async function streaming() {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(
    <App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />,
  );
  await userEvent.type(await screen.findByLabelText("Ask UFO"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

beforeEach(() => {
  useStreamFake();
});

test("the stream replays every fixture row the surface emits", async () => {
  const stream = await streaming();
  for (const row of fixture.filter((candidate) => candidate.event !== "parked")) {
    stream.emit(row.event, JSON.parse(row.data));
  }
  expect(await screen.findByText(payload<{ text: string }>("terminal").text)).toBeTruthy();
  expect(payload<{ files: { filename: string }[] }>("files").files[0].filename).toBe(
    "quarterly report.pdf",
  );
});

test("a parked fixture row lands as the turn's own notice", async () => {
  const stream = await streaming();
  stream.emit("message", payload("message"));
  stream.emit("parked", payload("parked"));
  const parked = payload<{ message: string }>("parked");
  expect(await screen.findByText(new RegExp(parked.message.slice(0, 20)))).toBeTruthy();
});
