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
  "/slots": () =>
    json({
      slots: [
        { id: "changes", label: "Changes", icon: "diff", kind: "changes", count: 0 },
      ],
    }),
});

function open() {
  return render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
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

test("a conversation reloaded while its turn runs shows the prompt, says so, and tails the turn", async () => {
  wire(transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }));
  open();

  expect(await screen.findByText("Review PR 1268.")).toBeTruthy();
  expect(await screen.findByText("Thinking…")).toBeTruthy();
  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + TURN_ID + "/stream");

  StreamFake.last().emit("tool", { tool: "bash", preview: "gh pr view" });
  expect(await screen.findByText("bash gh pr view")).toBeTruthy();
  expect(screen.queryByText("Thinking…")).toBeNull();

  StreamFake.last().emit("terminal", {
    status: "done",
    text: "Reviewed it.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });
  expect(await screen.findByText("Reviewed it.")).toBeTruthy();
  expect(StreamFake.last().closed).toBe(true);
});

test("a conversation reloaded mid-turn holds the composer, so the turn is tailed once", async () => {
  const { calls } = wire({
    ...transcript({ messages: [{ role: "user", text: "Review PR 1268." }], turn: TURN_ID }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  open();

  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  const send = screen.getByRole("button", { name: "Send" });
  expect((send as HTMLButtonElement).disabled).toBe(true);

  // A folded follow-up returns the running turn's own id, so a second attach would leave two
  // EventSources writing one live turn — every chunk doubled, and the reply recorded twice.
  await userEvent.type(screen.getByLabelText("Message the agent"), "and again");
  await userEvent.click(send);
  expect(calls.filter((url) => url.includes("/chat?conversation=")).length).toBe(0);
  expect(StreamFake.opened.length).toBe(1);

  StreamFake.last().emit("terminal", {
    status: "done",
    text: "Reviewed it.",
    model: "opus",
    tokens: 9,
    cost_micro_usd: 1_000_000,
  });

  await screen.findByText("Reviewed it.");
  await waitFor(() => expect((send as HTMLButtonElement).disabled).toBe(false));
  expect(StreamFake.opened.length).toBe(1);
});

test("a settled conversation tails nothing", async () => {
  wire(
    transcript({
      messages: [
        { role: "user", text: "inspect it" },
        { role: "assistant", text: "The tests pass." },
      ],
    }),
  );
  open();

  expect(await screen.findByText("The tests pass.")).toBeTruthy();
  expect(StreamFake.opened.length).toBe(0);
  expect(screen.queryByText("Thinking…")).toBeNull();
});

test("a reloaded conversation links its subagent conversation", async () => {
  const conversationId = "66666666-6666-4666-8666-666666666666";
  wire(
    transcript({
      messages: [
        {
          role: "assistant",
          text: "Done.",
          subagents: [{ profile: "general_purpose", conversation_id: conversationId }],
        },
      ],
    }),
  );
  open();

  const card = await screen.findByRole("link", { name: /Subagent · general_purpose/ });
  expect(card.getAttribute("href")).toBe(
    "#/subagents/general_purpose/conversations/" + conversationId,
  );
});

test("a conversation opens its file changes and returns to chat", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () =>
      json({
        type: "changes",
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
    ...transcript(),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Changes" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID + "?slot=changes");
  expect(await screen.findByText("/workspace/ufo/src/answer.ts")).toBeTruthy();
  const log = screen.getByTestId("log");
  expect(log.parentElement?.children).toHaveLength(2);
  expect(log.parentElement?.parentElement?.children).toHaveLength(2);
  expect(document.querySelector('[data-slot-icon="diff"]')).toBeTruthy();
  expect(screen.getByText("-old").className).toContain("bg-attention/25");
  expect(screen.getByText("+new").className).toContain("bg-link/10");
  expect(
    screen.getAllByText("--- before").every((line) => !line.className.includes("bg-attention/25")),
  ).toBe(true);
  expect(screen.getByText("This diff is truncated.")).toBeTruthy();
  expect(screen.getByText("Some changes may not be shown.")).toBeTruthy();

  await userEvent.click(within(screen.getByRole("main")).getByRole("button", { name: "Close slot" }));
  expect(location.hash).toBe("#/c/" + CONVO_ID);
});

test("multiple slot types coexist and sources render as external links", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/sources"]: () =>
      json({
        type: "sources",
        sources: [
          {
            url: "https://example.com/report",
            title: "Quarterly report",
            snippet: "**The retrieved result.**",
            published_date: "2026-08-01",
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          { id: "changes", label: "Changes", icon: "diff", kind: "changes", count: 2 },
          { id: "sources", label: "Sources", icon: "link", kind: "sources", count: 1 },
        ],
      }),
  });
  open();

  expect(await screen.findByRole("button", { name: "Changes 2" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Sources 1" }));

  expect(location.hash).toBe("#/c/" + CONVO_ID + "?slot=sources");
  const source = await screen.findByRole("link", { name: "Quarterly report" });
  expect(source.getAttribute("href")).toBe("https://example.com/report");
  expect(source.getAttribute("target")).toBe("_blank");
  const summary = screen.getByText("Summary");
  const disclosure = summary.closest("details") as HTMLDetailsElement;
  expect(disclosure.open).toBe(false);
  await userEvent.click(summary);
  expect(disclosure.open).toBe(true);
  expect(screen.getByText("The retrieved result.").closest("strong")).toBeTruthy();
});

test("files slot previews and downloads a workspace file in place", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/files"]: () =>
      json({
        type: "files",
        files: [
          {
            path: "notes/brief draft.md",
            size_bytes: 18,
            modified_at: "2026-08-06T12:00:00Z",
            preview: null,
          },
        ],
        truncated: false,
      }),
    ["/conversations/" + CONVO_ID + "/files/notes/brief%20draft.md"]: () =>
      new Response("# Brief\n\nShip it.\n"),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "files", label: "Files", icon: "file", kind: "files", count: 1 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Files 1" }));
  const file = await screen.findByRole("button", { name: "notes/brief draft.md" });
  const download = screen.getByRole("link", { name: "Download" });
  expect(download.getAttribute("href")).toBe(
    "/surface/web/agents/" +
      AGENT.id +
      "/conversations/" +
      CONVO_ID +
      "/files/notes/brief%20draft.md",
  );

  await userEvent.click(file);
  await waitFor(() =>
    expect(document.querySelector("aside pre")?.textContent).toBe("# Brief\n\nShip it.\n"),
  );
});

test("files slot lazily renders raster thumbnails and expands without text decoding", async () => {
  const imagePath =
    "/surface/web/agents/" +
    AGENT.id +
    "/conversations/" +
    CONVO_ID +
    "/files/images/chart.png?preview=signed";
  let textReads = 0;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/files"]: () =>
      json({
        type: "files",
        files: [
          {
            path: "images/chart.png",
            size_bytes: 2048,
            modified_at: "2026-08-06T12:00:00Z",
            preview: { type: "image", media_type: "image/png", url: imagePath },
          },
        ],
        truncated: false,
      }),
    ["/conversations/" + CONVO_ID + "/files/images/chart.png"]: () => {
      textReads += 1;
      return new Response("not text");
    },
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "files", label: "Files", icon: "file", kind: "files", count: 1 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Files 1" }));
  const thumbnail = await screen.findByRole("img", { name: "Thumbnail of images/chart.png" });
  expect(thumbnail.getAttribute("src")).toBe(imagePath);
  expect(thumbnail.getAttribute("loading")).toBe("lazy");

  await userEvent.click(screen.getByRole("button", { name: "images/chart.png" }));
  const expanded = await screen.findByRole("img", { name: "Preview of images/chart.png" });
  expect(expanded.getAttribute("src")).toBe(imagePath);
  expect(textReads).toBe(0);
  expect(screen.getByRole("link", { name: "Download" }).getAttribute("download")).toBe(
    "chart.png",
  );
});

test("artifacts slot renders durable shared outputs with their download metadata", async () => {
  const url = "https://ufo.example/artifacts/chart.png?token=signed";
  wire({
    ["/conversations/" + CONVO_ID + "/slots/artifacts"]: () =>
      json({
        type: "artifacts",
        artifacts: [
          {
            filename: "chart.png",
            subject: "The final chart",
            media_type: "image/png",
            size_bytes: 2048,
            created_at: "2026-08-06T12:00:00Z",
            url,
            preview: {
              type: "image",
              media_type: "image/png",
              url: "/artifacts/preview/chart.png?token=signed",
            },
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          {
            id: "artifacts",
            label: "Artifacts",
            icon: "artifact",
            kind: "artifacts",
            count: 1,
          },
        ],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Artifacts 1" }));
  const artifact = await screen.findByRole("link", { name: "chart.png" });
  expect(artifact.getAttribute("href")).toBe(url);
  expect(artifact.getAttribute("download")).toBe("chart.png");
  expect(screen.getByText("The final chart")).toBeTruthy();
  expect(
    document.querySelector('aside img[src="/artifacts/preview/chart.png?token=signed"]'),
  ).toBeTruthy();
});

test("tasks slot renders durable checklist progress and task status", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/tasks"]: () =>
      json({
        type: "tasks",
        title: "Ship slots",
        tasks: [
          { description: "Define the payload", status: "completed" },
          { description: "Render the board", status: "in_progress" },
          { description: "Verify the flow", status: "pending" },
        ],
        total_count: 3,
        completed_count: 1,
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "tasks", label: "Tasks", icon: "task", kind: "tasks", count: 3 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Tasks 3" }));
  expect(await screen.findByRole("heading", { name: "Ship slots" })).toBeTruthy();
  expect(screen.getByText("1 of 3 completed.")).toBeTruthy();
  expect(screen.getByText("Define the payload")).toBeTruthy();
  expect(screen.getByText("Completed")).toBeTruthy();
  expect(screen.getByText("In progress")).toBeTruthy();
  expect(screen.getByText("Pending")).toBeTruthy();
});

test("tasks slot preserves an empty truncated board's context", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/tasks"]: () =>
      json({
        type: "tasks",
        title: "Bounded board",
        tasks: [],
        total_count: 0,
        completed_count: 0,
        truncated: true,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "tasks", label: "Tasks", icon: "task", kind: "tasks", count: 0 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Tasks" }));
  expect(await screen.findByRole("heading", { name: "Bounded board" })).toBeTruthy();
  expect(screen.getByText("0 of 0 completed.")).toBeTruthy();
  expect(await screen.findByText("No tasks.")).toBeTruthy();
  expect(screen.getByText("Some tasks may not be shown.")).toBeTruthy();
});

test("sites slot renders hosted links with visibility and state", async () => {
  const url = "https://ufo.example/sites/signed";
  wire({
    ["/conversations/" + CONVO_ID + "/slots/sites"]: () =>
      json({
        type: "sites",
        sites: [
          {
            name: "team-dashboard",
            url,
            visibility: "workspace",
            created_at: "2026-08-01T12:00:00Z",
            updated_at: "2026-08-06T12:00:00Z",
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [{ id: "sites", label: "Sites", icon: "link", kind: "sites", count: 1 }],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Sites 1" }));
  expect(await screen.findByText("team-dashboard")).toBeTruthy();
  expect(screen.getByText(/workspace · Created/)).toBeTruthy();
  const link = screen.getByRole("link", { name: "Open site" });
  expect(link.getAttribute("href")).toBe(url);
  expect(link.getAttribute("target")).toBe("_blank");
});

test("automations slot renders cadence, state, and visible description", async () => {
  wire({
    ["/conversations/" + CONVO_ID + "/slots/automations"]: () =>
      json({
        type: "automations",
        automations: [
          {
            name: "daily-brief",
            description: "Send the morning brief.",
            schedule: "0 9 * * *",
            paused: false,
            next_run_at: "2026-08-08T09:00:00Z",
            last_run_at: "2026-08-07T09:00:00Z",
            latest_status: "done",
            latest_response: "Brief delivered.",
            created_at: "2026-08-01T12:00:00Z",
            updated_at: "2026-08-06T12:00:00Z",
          },
        ],
        truncated: false,
      }),
    ...transcript(),
    "/slots": () =>
      json({
        slots: [
          {
            id: "automations",
            label: "Automations",
            icon: "calendar",
            kind: "automations",
            count: 1,
          },
        ],
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Automations 1" }));
  expect(await screen.findByText("daily-brief")).toBeTruthy();
  expect(screen.getByText("Send the morning brief.")).toBeTruthy();
  expect(screen.getByText("Running")).toBeTruthy();
  expect(screen.getByText("Brief delivered.")).toBeTruthy();
  expect(screen.getByText(/Last .* · done/)).toBeTruthy();
  expect(screen.getByText(/0 9 \* \* \* · Next/)).toBeTruthy();
  expect(document.querySelector('[data-slot-icon="calendar"]')).toBeTruthy();
});

test.each([
  [200, { type: "changes", changes: [], truncated: false }, "No changes."],
  [200, { type: "changes", changes: [], truncated: true }, "Some changes may not be shown."],
  [404, null, "This conversation is not shared with you."],
])("changes renders status %s", async (status, payload, message) => {
  location.hash =
    "#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/changes";
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () =>
      payload === null ? new Response("no", { status }) : json(payload),
    ...transcript(),
  });
  open();

  expect(await screen.findByText(message)).toBeTruthy();
  expect(screen.getByText("Changes")).toBeTruthy();
});

test("changes refresh after another file result lands", async () => {
  location.hash =
    "#/agents/" + AGENT.id + "/conversations/" + CONVO_ID + "/slots/changes";
  let loads = 0;
  wire({
    ["/conversations/" + CONVO_ID + "/slots/changes"]: () => {
      loads += 1;
      return json({
        type: "changes",
        changes:
          loads === 1
            ? []
            : [{ path: "src/late.ts", patch: "+export const ready = true;", truncated: false }],
        truncated: false,
      });
    },
    ...transcript(),
  });
  open();

  expect(await screen.findByText("No changes.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("src/late.ts")).toBeTruthy();
  expect(loads).toBe(2);
});

test("slot counts refresh when a turn settles", async () => {
  let slotLoads = 0;
  wire({
    ...transcript(),
    "/slots": () => {
      slotLoads += 1;
      return json({
        slots: [
          {
            id: "changes",
            label: "Changes",
            icon: "diff",
            kind: "changes",
            count: slotLoads === 1 ? 0 : 1,
          },
        ],
      });
    },
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "edit" }),
  });
  open();

  await screen.findByRole("button", { name: "Changes" });
  await userEvent.type(screen.getByLabelText("Message the agent"), "edit it");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("terminal", {
    status: "done",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });

  expect(await screen.findByRole("button", { name: "Changes 1" })).toBeTruthy();
  expect(slotLoads).toBe(2);
});

test("a slot URL opens a conversation absent from the chat rail", async () => {
  const child = "66666666-6666-4666-8666-666666666666";
  const root = "77777777-7777-4777-8777-777777777777";
  location.hash =
    "#/agents/" + AGENT.id + "/conversations/" + child + "/slots/changes?root=" + root;
  wire({
    ["/conversations/" + child + "/slots/changes?root=" + root]: () =>
      json({
        type: "changes",
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
  render(<App agents={[AGENT, SECOND]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
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
