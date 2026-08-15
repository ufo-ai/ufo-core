import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

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
  title: "Morning digest",
  origin: "#eng",
  agent_id: AGENT.id,
  agent_name: "assistant",
  fired_at: "2026-08-14T09:00:00+00:00",
  status: "done",
  task: "morning-digest",
  text: "12 items, **2 stale**.",
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
  ],
};

const FAILED_RUN = {
  ...RUN,
  turn_id: "1c8f3e54-6b97-4a20-8d4e-9f75b13c8d26",
  title: null,
  task: "weekly-numbers",
  fired_at: "2026-08-14T07:00:00+00:00",
  status: "failed",
  text: "",
  artifacts: [],
};

const EARLIER_RUN = {
  ...RUN,
  turn_id: "2d9a4f65-7ca8-4b31-9e5f-a086c24d9e37",
  title: "Standup notes",
  task: null,
  fired_at: "2026-08-13T09:00:00+00:00",
  status: "running",
  text: "",
  artifacts: [],
};

beforeEach(() => {
  useStreamFake();
});

function mountRadarSection() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="radar" />
    </MainAgentProvider>,
  );
}

test("the feed reads as editions: datelines, headline links, bylines, and shared files", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN, FAILED_RUN, EARLIER_RUN], older: null }),
  });
  mountRadarSection();

  expect(await screen.findByRole("heading", { level: 2, name: "Aug 14 2026" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Aug 13 2026" })).toBeTruthy();

  const headline = screen.getByRole("link", { name: "Morning digest" });
  expect(headline.getAttribute("href")).toBe("#/c/" + CONVO_ID);
  expect(screen.getAllByText("assistant").length).toBeGreaterThan(0);
  expect(screen.getAllByText("#eng").length).toBeGreaterThan(0);
  expect(screen.getByText("2 stale").tagName).toBe("STRONG");

  expect(screen.getByRole("link", { name: "weekly-numbers" })).toBeTruthy();
  expect(screen.getByText("Failed")).toBeTruthy();
  expect(screen.getByText("Running")).toBeTruthy();

  const preview = screen.getByRole("img", { name: "the queue" });
  expect(preview.getAttribute("src")).toBe("/dl/queue.png?preview");
  expect(preview.closest("a")?.getAttribute("href")).toBe("/dl/queue.png");
  expect(screen.getByText("brief.pdf").closest("a")?.getAttribute("href")).toBe("/dl/brief.pdf");
});

test("a story's task opens the task record where the prompt is read", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
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
  expect(await screen.findByRole("link", { name: "Morning digest" })).toBeTruthy();
});

test("the feed walks older pages by cursor", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": (url) => {
      reads.push(url);
      return url.includes("after=")
        ? json({ runs: [EARLIER_RUN], older: null, newer: "newer|x" })
        : json({ runs: [RUN], older: "older|x", newer: null });
    },
  });
  mountRadarSection();

  expect(await screen.findByRole("link", { name: "Morning digest" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));

  expect(await screen.findByText("Running")).toBeTruthy();
  expect(reads.some((read) => read.includes("after=older%7Cx"))).toBe(true);
});
