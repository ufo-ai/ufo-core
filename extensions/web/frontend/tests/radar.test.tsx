import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  CONVO_ID,
  PlacedSection,
  TASK_KIND,
  json,
  useStreamFake,
  wire,
} from "./harness";

const RUN = {
  turn_id: "0b7e2d43-5a86-4f19-9c3d-8e64a02b7c15",
  conversation_id: CONVO_ID,
  agent_id: AGENT.id,
  fired_at: "2026-08-14T09:00:00+00:00",
  status: "done",
  task: "morning-digest",
  surface: "slack",
  source: "https://acme.slack.com/archives/C42/p1",
  text: "",
  artifacts: [
    {
      filename: "queue.png",
      subject: "the queue",
      media_type: "image/png",
      size_bytes: 3,
      url: "/dl/queue.png",
      preview_url: "/dl/queue.png?preview",
    },
    {
      filename: "brief.pdf",
      subject: null,
      media_type: "application/pdf",
      size_bytes: 2048,
      url: "/dl/brief.pdf",
      preview_url: null,
    },
    {
      filename: "notes.md",
      subject: null,
      media_type: "text/markdown",
      size_bytes: 24,
      url: "/dl/notes.md",
      preview_url: null,
    },
  ],
};

const FAILED_RUN = {
  ...RUN,
  turn_id: "1c8f3e54-6b97-4a20-8d4e-9f75b13c8d26",
  task: "weekly-numbers",
  fired_at: "2026-08-14T07:00:00+00:00",
  status: "failed",
  text: "The roll-up source timed out.",
  artifacts: [],
};

const QUIET_RUN = {
  ...RUN,
  turn_id: "3e0b5a76-8db9-4c42-af60-b197d35e0f48",
  task: "quiet-check",
  fired_at: "2026-08-14T08:00:00+00:00",
  status: "done",
  text: "",
  artifacts: [],
};

const STOPPED_RUN = {
  ...RUN,
  turn_id: "2d9a4f65-7ca8-4b31-9e5f-a086c24d9e37",
  task: null,
  surface: "web",
  source: null,
  fired_at: "2026-08-13T09:00:00+00:00",
  status: "cancelled",
  text: "",
  artifacts: [],
};

/** A run's dateline reads as its distance from now, so the runs these fixtures hold have to
 *  stand at a fixed distance from it. */
beforeEach(() => {
  useStreamFake();
  vi.setSystemTime(new Date("2026-08-14T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

function mountRadarSection() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="radar" />
    </MainAgentProvider>,
  );
}

test("a story is what a run made or how it went wrong, never what it said", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN, FAILED_RUN, STOPPED_RUN], older: null }),
    "/dl/notes.md": () => new Response("### Standup\n\nTwo blockers cleared."),
  });
  mountRadarSection();

  expect(await screen.findByRole("heading", { level: 2, name: "Aug 14 2026" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Aug 13 2026" })).toBeTruthy();

  const headline = screen.getByRole("heading", { level: 3, name: "morning-digest" });
  expect(headline.querySelector("button")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 3, name: "Scheduled run" })).toBeTruthy();

  const preview = screen.getByRole("img", { name: "the queue" });
  expect(preview.getAttribute("src")).toBe("/dl/queue.png?preview");
  expect(preview.closest("a")?.getAttribute("href")).toBe("/dl/queue.png");
  expect(screen.getByText("brief.pdf").closest("a")?.getAttribute("href")).toBe("/dl/brief.pdf");
  expect(await screen.findByText("Two blockers cleared.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  expect(screen.queryByText("notes.md")).toBeNull();

  const conversations = screen.getAllByRole("link", { name: "Conversation" });
  expect(conversations[0].getAttribute("href")).toBe("#/c/" + CONVO_ID);
  const threads = screen.getAllByRole("link", { name: "Slack" });
  expect(threads[0].textContent).toBe("Slack ↗");
  expect(threads[0].getAttribute("href")).toBe(RUN.source);
  expect(screen.getAllByText(/ago$/).length).toBeGreaterThan(0);

  expect(screen.getByText("Failed")).toBeTruthy();
  expect(screen.getByText("The roll-up source timed out.")).toBeTruthy();
  expect(screen.getByText("Stopped")).toBeTruthy();
});

test("the feed draws every run the page holds", async () => {
  wire({ "/workspace/radar": () => json({ runs: [QUIET_RUN], older: "older|x" }) });
  mountRadarSection();

  expect(await screen.findByRole("heading", { level: 3, name: "quiet-check" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Older" })).toBeTruthy();
});

test("a story's task opens the task record where the prompt is read", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("All quiet on the queue."),
    "/objects/scheduled_task/morning-digest": (url) => {
      reads.push(url);
      return json({
        ...TASK_KIND,
        name: "morning-digest",
        summary: "0 9 * * * — the queue check",
        spec: { schedule: "0 9 * * *", prompt: "check the queue", paused: false },
        status: { next_run_at: "2026-08-15T09:00:00+00:00", paused: false },
        links: [],
        created_at: "2026-08-01T09:00:00Z",
        updated_at: "2026-08-01T09:00:00Z",
      });
    },
  });
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));

  expect(await screen.findByRole("heading", { name: "morning-digest" })).toBeTruthy();
  expect(await screen.findByText("check the queue")).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT.id);

  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByRole("button", { name: "morning-digest" })).toBeTruthy();
});

test("the feed walks older pages by cursor", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": (url) => {
      reads.push(url);
      return url.includes("after=")
        ? json({ runs: [STOPPED_RUN], older: null, newer: "newer|x" })
        : json({ runs: [RUN], older: "older|x", newer: null });
    },
    "/dl/notes.md": () => new Response("All quiet on the queue."),
  });
  mountRadarSection();

  expect(await screen.findByRole("button", { name: "morning-digest" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));

  expect(await screen.findByText("Stopped")).toBeTruthy();
  expect(reads.some((read) => read.includes("after=older%7Cx"))).toBe(true);
});
