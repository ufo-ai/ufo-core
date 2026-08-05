import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  StreamFake,
  TURN_ID,
  json,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

const transcript = (payload: unknown = { messages: [] }) => ({
  "/api/chats": () => json({ chats: [CHAT_ROW] }),
  "/transcript": () => json(payload),
});

function open() {
  return render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
}

test("an empty conversation states it, and the composer sends a message and streams the reply", async () => {
  wire({
    ...transcript(),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();

  expect(await screen.findByText("No messages in this conversation yet.")).toBeTruthy();

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
  expect(await screen.findByText("opus · 12 tok · $2.00")).toBeTruthy();
  expect(StreamFake.last().closed).toBe(true);
});

test("a reloaded conversation keeps the activity that produced its reply", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "inspect it" },
        {
          role: "assistant",
          text: "The tests pass.",
          events: [
            {
              kind: "tool",
              name: "bash",
              preview: '{"command":"uv run pytest"}',
              description: "Running the focused tests",
            },
            {
              kind: "skill",
              name: "coding",
              preview: "",
              description: "",
            },
          ],
        },
      ],
    }),
  );
  open();

  const summary = await screen.findByText("2 tool calls");
  await userEvent.click(summary);
  expect(screen.getByText("Running the focused tests")).toBeTruthy();
  expect(screen.getByText("Loaded skill · coding")).toBeTruthy();
});

test("a conversation opens its file changes and returns to chat", async () => {
  wire({
    ...transcript(),
    ["/conversations/" + CONVO_ID + "/changes"]: () =>
      json({
        changes: [
          {
            path: "/workspace/demo.py",
            patch: "--- before\n+++ after\n@@ -1 +1 @@\n-old demo\n+new demo",
            truncated: false,
          },
          {
            path: "/workspace/ufo/src/answer.ts",
            patch: "--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new",
            truncated: true,
          },
        ],
        truncated: true,
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Changes" }));
  expect(location.hash).toBe(
    "#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/changes",
  );
  expect(await screen.findByText("/workspace/ufo/src/answer.ts")).toBeTruthy();
  expect(screen.getByText("-old").className).toContain("bg-attention/25");
  expect(screen.getByText("+new").className).toContain("bg-link/10");
  expect(
    screen.getAllByText("--- before").every((line) => !line.className.includes("bg-attention/25")),
  ).toBe(true);
  expect(screen.getByText("This diff is truncated.")).toBeTruthy();
  expect(screen.getByText("Some changes may not be shown.")).toBeTruthy();

  await userEvent.click(within(screen.getByRole("main")).getByRole("button", { name: AGENT.name }));
  expect(location.hash).toBe("#/agents/" + AGENT.id);
});

test.each([
  [200, { changes: [], truncated: false }, "No changes."],
  [200, { changes: [], truncated: true }, "Some changes may not be shown."],
  [404, null, "This conversation is not shared with you."],
])("changes renders status %s", async (status, payload, message) => {
  location.hash = "#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/changes";
  wire({
    ...transcript(),
    ["/conversations/" + CONVO_ID + "/changes"]: () =>
      payload === null ? new Response("no", { status }) : json(payload),
  });
  open();

  expect(await screen.findByText(message)).toBeTruthy();
});

test("changes refresh after another file result lands", async () => {
  location.hash = "#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/changes";
  let loads = 0;
  wire({
    ...transcript(),
    ["/conversations/" + CONVO_ID + "/changes"]: () => {
      loads += 1;
      return json({
        changes:
          loads === 1
            ? []
            : [{ path: "src/late.ts", patch: "+export const ready = true;", truncated: false }],
        truncated: false,
      });
    },
  });
  open();

  expect(await screen.findByText("No changes.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("src/late.ts")).toBeTruthy();
  expect(loads).toBe(2);
});

test("a changes URL opens a conversation absent from the chat rail", async () => {
  const child = "66666666-6666-4666-8666-666666666666";
  const root = "77777777-7777-4777-8777-777777777777";
  location.hash =
    "#/agents/" + AGENT.id + "/conversations/" + child + "/changes?root=" + root;
  wire({
    ["/conversations/" + child + "/changes?root=" + root]: () =>
      json({
        changes: [{ path: "/workspace/repo/child.py", patch: "+child\n", truncated: false }],
        truncated: false,
      }),
  });
  open();

  expect(await screen.findByText("/workspace/repo/child.py")).toBeTruthy();
});

test("the composer is disabled while a turn streams and re-enabled when it lands", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");

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

test("a failed post states the error instead of opening a stream", async () => {
  wire({
    ...transcript(),
    "/chat": () => new Response("nope", { status: 500 }),
  });
  open();
  await screen.findByText("No messages in this conversation yet.");
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
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
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

test("a streamed chunk never steals focus from where the member put it", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const elsewhere = screen.getByRole("button", { name: "Agents" });
  elsewhere.focus();
  StreamFake.last().emit("message", { text: "chunk" });
  await screen.findByText("chunk");
  expect(document.activeElement).toBe(elsewhere);
});

test("streaming keeps the log pinned at the bottom but never yanks a reader back down", async () => {
  const { fireEvent } = await import("@testing-library/react");
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });

  log.scrollTop = 100;
  fireEvent.scroll(log);
  StreamFake.last().emit("message", { text: "while reading" });
  await screen.findByText("while reading");
  expect(log.scrollTop).toBe(100);

  log.scrollTop = 700;
  fireEvent.scroll(log);
  StreamFake.last().emit("message", { text: " more" });
  await screen.findByText(/more/);
  expect(log.scrollTop).toBe(1000);
});

test("sending while scrolled up re-pins the log to the bottom", async () => {
  const { fireEvent } = await import("@testing-library/react");
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });
  log.scrollTop = 100;
  fireEvent.scroll(log);

  await userEvent.type(screen.getByLabelText("Message the agent"), "back to now");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("back to now");
  expect(log.scrollTop).toBe(1000);
});

test("answering a question while scrolled up re-pins the log to the bottom", async () => {
  const { fireEvent } = await import("@testing-library/react");
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "asking" }],
      question: {
        turn_id: TURN_ID,
        title: "Pick one",
        questions: [{ question: "Which?", options: [{ label: "left" }, { label: "right" }] }],
      },
    }),
    "/chat": () => json({ turn_id: "44444444-4444-4444-8444-444444444444", body: "left" }),
  });
  open();
  await screen.findByText("asking");

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });
  log.scrollTop = 100;
  fireEvent.scroll(log);

  await userEvent.click(screen.getByRole("button", { name: "left" }));
  await screen.findByText("left");
  expect(log.scrollTop).toBe(1000);
});

test("switching conversations remounts the log so scroll state never leaks across", async () => {
  const { fireEvent } = await import("@testing-library/react");
  const other = {
    ...CHAT_ROW,
    conversation_id: "66666666-6666-4666-8666-666666666666",
    agent_id: SECOND_ID,
    agent_name: "second",
    title: "The second thread",
  };
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW, other] }),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} />);
  await screen.findByText("No messages in this conversation yet.");

  const first = screen.getByTestId("log");
  Object.defineProperty(first, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(first, "clientHeight", { configurable: true, value: 300 });
  first.scrollTop = 100;
  fireEvent.scroll(first);

  await userEvent.click(screen.getByRole("button", { name: /The second thread/ }));
  await screen.findByText("No messages in this conversation yet.");
  expect(document.activeElement).toBe(screen.getByLabelText("Message the agent"));
  const fresh = screen.getByTestId("log");
  expect(fresh).not.toBe(first);

  Object.defineProperty(fresh, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(fresh, "clientHeight", { configurable: true, value: 300 });
  await userEvent.type(screen.getByLabelText("Message the agent"), "hello there");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("hello there");
  expect(fresh.scrollTop).toBe(1000);
});

test("the log opens pinned: loading a transcript lands at the bottom untouched", async () => {
  let release: (value: Response) => void = () => {};
  wire({
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/transcript": () => new Promise<Response>((resolve) => (release = resolve)),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }),
  });
  open();

  const log = await screen.findByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });

  release(json({ messages: [{ role: "assistant", text: "history" }] }));
  await screen.findByText("history");
  expect(log.scrollTop).toBe(1000);
});

test("a reader inside the tolerance band still counts as at the bottom", async () => {
  const { fireEvent } = await import("@testing-library/react");
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });
  log.scrollTop = 690;
  fireEvent.scroll(log);

  StreamFake.last().emit("message", { text: "nudged" });
  await screen.findByText("nudged");
  expect(log.scrollTop).toBe(1000);
});

test("sending returns focus to the composer instead of stranding it on the page", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  const input = screen.getByLabelText("Message the agent");
  await userEvent.type(input, "hi");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(document.activeElement).toBe(input);
});

test("answering a question returns focus to the composer", async () => {
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "asking" }],
      question: {
        turn_id: TURN_ID,
        title: "Pick one",
        questions: [{ question: "Which?", options: [{ label: "left" }, { label: "right" }] }],
      },
    }),
    "/chat": () => json({ turn_id: "44444444-4444-4444-8444-444444444444", body: "left" }),
  });
  open();
  await userEvent.click(await screen.findByRole("button", { name: "left" }));
  await screen.findByText("left");
  expect(document.activeElement).toBe(screen.getByLabelText("Message the agent"));
});

test("a second answer clicked mid-stream neither posts nor yanks the reader", async () => {
  const { fireEvent } = await import("@testing-library/react");
  const posts: string[] = [];
  wire({
    ...transcript({
      messages: [{ role: "assistant", text: "asking" }],
      question: {
        turn_id: TURN_ID,
        title: "Two things",
        questions: [
          { question: "First?", options: [{ label: "alpha" }, { label: "beta" }] },
          { question: "Second?", options: [{ label: "gamma" }, { label: "delta" }] },
        ],
      },
    }),
    "/chat": (url) => {
      posts.push(url);
      return json({ turn_id: "44444444-4444-4444-8444-444444444444", body: "alpha" });
    },
  });
  open();
  await userEvent.click(await screen.findByRole("button", { name: "alpha" }));
  await waitFor(() => expect(posts.length).toBe(1));

  const log = screen.getByTestId("log");
  Object.defineProperty(log, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(log, "clientHeight", { configurable: true, value: 300 });
  log.scrollTop = 100;
  fireEvent.scroll(log);

  await userEvent.click(screen.getByRole("button", { name: "gamma" }));
  expect(posts.length).toBe(1);
  StreamFake.last().emit("message", { text: "still streaming" });
  await screen.findByText(/still streaming/);
  expect(log.scrollTop).toBe(100);
});

test("agent markdown renders as elements while the member's text stays literal", async () => {
  wire({ ...transcript(), "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "hello" }) });
  open();
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "**hi**");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  expect(screen.getByText("**hi**")).toBeTruthy();
  StreamFake.last().emit("message", { text: "see [docs][ref]\n\n" });
  StreamFake.last().emit("message", { text: "**mid**" });
  expect((await screen.findByText("mid")).tagName).toBe("STRONG");
  StreamFake.last().emit("message", { text: "\n\n**bold**\n\n[ref]: https://example.com/d\n" });
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });

  expect((await screen.findByText("bold")).tagName).toBe("STRONG");
  const agentSide = screen.getByText("bold").closest("[data-role=agent]")!;
  expect(agentSide.className).not.toContain("whitespace-pre-wrap");
  const mineSide = screen.getByText("**hi**").closest("[data-role=me]")!;
  expect(mineSide.className).toContain("whitespace-pre-wrap");
  const link = await screen.findByRole("link", { name: "docs" });
  expect(link.getAttribute("href")).toBe("https://example.com/d");
  expect(screen.getByText("**hi**").textContent).toBe("**hi**");
});
