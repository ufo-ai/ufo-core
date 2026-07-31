import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, StreamFake, TURN_ID, json, useStreamFake, wire } from "./harness";

beforeEach(() => {
  useStreamFake();
});

const transcript = (payload: unknown = { messages: [] }) => ({
  "/transcript": () => json(payload),
});

function open() {
  return render(<App agents={[AGENT]} member={MEMBER} />);
}

test("an empty conversation states it, and the composer sends a message and streams the reply", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID }),
  });
  open();

  expect(await screen.findByText("No conversation with assistant yet.")).toBeTruthy();

  await userEvent.type(screen.getByLabelText("Message the agent"), "hello");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");
  expect(screen.getByText("hello")).toBeTruthy();

  StreamFake.last().emit("message", { text: "one " });
  StreamFake.last().emit("message", { text: "two" });
  expect(await screen.findByText("one two")).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 12,
    cost_micro_usd: 2_000_000,
  });
  expect(await screen.findByText("opus · 12 tok · $2.000000")).toBeTruthy();
  expect(StreamFake.last().closed).toBe(true);
});

test("the composer is disabled while a turn streams and re-enabled when it lands", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID }) });
  open();
  await screen.findByText("No conversation with assistant yet.");

  const send = screen.getByRole("button", { name: "Send" });
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(send);
  await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(true));

  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });
  await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
});

test("a stream that stalls past its retries states the loss and keeps what streamed", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID }) });
  open();
  await screen.findByText("No conversation with assistant yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const stream = StreamFake.last();
  stream.emit("message", { text: "partial" });
  for (let attempt = 0; attempt < 4; attempt += 1) stream.fail();
  expect(screen.queryByText("Connection lost — reload to see the reply.")).toBeNull();

  stream.fail();
  expect(await screen.findByText("Connection lost — reload to see the reply.")).toBeTruthy();
  expect(screen.getByText("partial")).toBeTruthy();
});

test("a failed post states the error instead of opening a stream", async () => {
  wire({
    ...transcript(),
    "/chat": () => new Response("nope", { status: 500 }),
  });
  open();
  await screen.findByText("No conversation with assistant yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));

  expect(await screen.findByText("Error 500 — try again.")).toBeTruthy();
  expect(StreamFake.opened.length).toBe(0);
});

test("a question handoff answers by its option button and posts the answer headers", async () => {
  const posts: RequestInit[] = [];
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "asking" }],
      question: {
        turn_id: TURN_ID,
        title: "Pick one",
        questions: [{ question: "Which?", options: [{ label: "left" }, { label: "right" }] }],
      },
    }),
    "/chat": (_url, init) => {
      posts.push(init ?? {});
      return json({ turn_id: "44444444-4444-4444-8444-444444444444", body: "left" });
    },
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "left" }));

  await waitFor(() => expect(posts.length).toBe(1));
  const headers = posts[0].headers as Record<string, string>;
  expect(headers["x-ufo-answer-turn"]).toBe(TURN_ID);
  expect(headers["x-ufo-answer-question"]).toBe("0");
  expect(await screen.findByText("left")).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("button", { name: "right" })).toBeNull());
});

test("a files frame lists each shared file with its size", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID }) });
  open();
  await screen.findByText("No conversation with assistant yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  StreamFake.last().emit("files", {
    files: [{ filename: "report.csv", url: "/dl/report.csv", size_bytes: 2048 }],
  });

  const link = await screen.findByRole("link", { name: "report.csv" });
  expect(link.getAttribute("href")).toBe("/dl/report.csv");
  expect(screen.getByText("· 2 kB")).toBeTruthy();
});

test("a credential handoff stores a value and drops the prompt it answered", async () => {
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "need a key" }],
      credentials: {
        sealed: "sealed-token",
        reason: "notion authenticates with this value.",
        prompts: [{ slot: "notion_token", prompt: "Notion token" }],
      },
    }),
    "/credentials": () => new Response("stored"),
  });
  open();

  expect(await screen.findByText("notion authenticates with this value.")).toBeTruthy();
  await userEvent.type(screen.getByPlaceholderText("notion_token"), "secret");
  await userEvent.click(screen.getByRole("button", { name: "Store" }));

  expect(await screen.findByText("Stored notion_token.")).toBeTruthy();
  expect(screen.queryByPlaceholderText("notion_token")).toBeNull();
});
