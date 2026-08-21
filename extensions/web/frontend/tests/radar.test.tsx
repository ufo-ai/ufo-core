import { render, screen, within } from "@testing-library/react";
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

/** What the report-digest skill wrote about the run below: the title the feed heads the entry
 *  with, the line under it, and the findings with whoever the report named beside each. */
const WRITTEN = {
  title: "Portal moves to app.example.com, reports endpoint breaks",
  summary: "Search dropped from 1.4s to 180ms; the old reports path dies September 1.",
  points: [
    { text: "Reports endpoint needs a workspace id", actor: "Theo Lindqvist" },
    { text: "Portal moved to app.example.com", actor: "Marshall Reed" },
  ],
};

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
  entry: WRITTEN,
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
  entry: null,
  artifacts: [],
};

/** A report published since the digest job last ran: it carries nothing written yet. */
const QUIET_RUN = {
  ...RUN,
  turn_id: "3e0b5a76-8db9-4c42-af60-b197d35e0f48",
  task: "quiet-check",
  fired_at: "2026-08-14T08:00:00+00:00",
  status: "done",
  text: "All quiet on the queue.",
  entry: null,
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
  entry: null,
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

/** The feed standing on one run's own address, which is what an entry and a story's dateline
 *  both link. */
function mountPinnedRun(run: { turn_id: string }) {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="radar" place={{ open: "run/" + run.turn_id }} />
    </MainAgentProvider>,
  );
}

test("an entry states what the report found and who contributed each line", async () => {
  wire({ "/workspace/radar": () => json({ runs: [RUN], older: null }) });
  mountRadarSection();

  expect(await screen.findByRole("heading", { level: 3, name: WRITTEN.title })).toBeTruthy();
  expect(screen.getByText(/Search dropped from 1.4s to 180ms/)).toBeTruthy();
  expect(screen.getByText(/ago$/).closest("p")?.textContent).toBe(
    "3h ago · Assistant · morning-digest",
  );

  const attributed = screen.getByText("Reports endpoint needs a workspace id").closest("li");
  expect(within(attributed as HTMLElement).getByText("Theo Lindqvist")).toBeTruthy();
  expect(screen.getByText("Marshall Reed")).toBeTruthy();
});

test("the whole entry opens the report it describes", async () => {
  wire({ "/workspace/radar": () => json({ runs: [RUN], older: null }) });
  mountRadarSection();

  const opened = await screen.findByRole("link", { name: new RegExp(WRITTEN.title) });
  expect(opened.getAttribute("href")).toBe("#/radar?open=run%2F" + RUN.turn_id);
});

test("a picture the report made is drawn beside the entry, cropped from its top", async () => {
  wire({ "/workspace/radar": () => json({ runs: [RUN], older: null }) });
  mountRadarSection();

  const picture = await screen.findByRole("presentation");
  expect(picture.getAttribute("src")).toBe("/dl/queue.png?preview");
  expect(picture.className).toContain("object-cover");
  expect(picture.className).toContain("object-top-left");
});

/** A run whose document was shared before the chart it drew. Both carry a picture — a rendered
 *  first page is minted for the document the same way a raster is for the chart — so share order
 *  alone would put the page on the rail. */
const PAGE_FIRST_RUN = {
  ...RUN,
  turn_id: "6b3f8da9-1ce2-4f75-b293-e4c8a6812f37",
  artifacts: [
    {
      filename: "brief.pdf",
      subject: null,
      media_type: "application/pdf",
      size_bytes: 2048,
      url: "/dl/brief.pdf",
      preview_url: "/dl/brief.png?preview",
    },
    {
      filename: "queue.png",
      subject: "the queue",
      media_type: "image/png",
      size_bytes: 3,
      url: "/dl/queue.png",
      preview_url: "/dl/queue.png?preview",
    },
  ],
};

test("the picture on the rail is one the report drew, not the first page of its paperwork", async () => {
  wire({ "/workspace/radar": () => json({ runs: [PAGE_FIRST_RUN], older: null }) });
  mountRadarSection();

  const picture = await screen.findByRole("presentation");
  expect(picture.getAttribute("src")).toBe("/dl/queue.png?preview");
});

/** A run that drew nothing is still represented: the page it published is the only picture it has. */
test("a report that drew no picture stands on the page it published", async () => {
  const paperwork = {
    ...PAGE_FIRST_RUN,
    turn_id: "7c4a9eb0-2df3-4086-a3a4-f5d9b7923048",
    artifacts: [PAGE_FIRST_RUN.artifacts[0]],
  };
  wire({ "/workspace/radar": () => json({ runs: [paperwork], older: null }) });
  mountRadarSection();

  const picture = await screen.findByRole("presentation");
  expect(picture.getAttribute("src")).toBe("/dl/brief.png?preview");
});

test("a report with no entry yet stands on its task and states nothing it cannot", async () => {
  wire({ "/workspace/radar": () => json({ runs: [QUIET_RUN], older: null }) });
  mountRadarSection();

  const entry = (await screen.findByRole("heading", { level: 3, name: "quiet-check" })).closest(
    "li",
  );
  expect(entry?.querySelector("ul")).toBeNull();
  expect(screen.queryByText("All quiet on the queue.")).toBeNull();
});

test("a run that did not end well is marked and says why", async () => {
  wire({ "/workspace/radar": () => json({ runs: [FAILED_RUN], older: null }) });
  mountRadarSection();

  expect(await screen.findByText(/Failed/)).toBeTruthy();
  expect(screen.getByText("The roll-up source timed out.")).toBeTruthy();
});

test("a run that did not end well says why, whatever was written about it", async () => {
  /** The writer only digests a run that ended well, so an entry on a failed run means a partial
   *  report was digested before the run stopped. What the member can act on is the reason it
   *  stopped, and the entry must not draw over it. */
  wire({
    "/workspace/radar": () =>
      json({ runs: [{ ...FAILED_RUN, entry: WRITTEN }], older: null }),
  });
  mountRadarSection();

  expect(await screen.findByText("The roll-up source timed out.")).toBeTruthy();
  expect(screen.queryByText(WRITTEN.summary)).toBeNull();
});

test("the feed draws every run the page holds", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN, QUIET_RUN, STOPPED_RUN], older: "older|x" }),
  });
  mountRadarSection();

  expect(await screen.findByRole("heading", { level: 3, name: WRITTEN.title })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 3, name: "quiet-check" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 3, name: "Scheduled run" })).toBeTruthy();
  expect(screen.getByText(/Stopped/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Older" })).toBeTruthy();
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
  });
  mountRadarSection();

  expect(await screen.findByRole("heading", { level: 3, name: WRITTEN.title })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));

  expect(await screen.findByText(/Stopped/)).toBeTruthy();
  expect(reads.some((read) => read.includes("after=older%7Cx"))).toBe(true);
});

test("a story is what a run made, never what it said", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("### Standup\n\nTwo blockers cleared."),
  });
  mountPinnedRun(RUN);

  const headline = await screen.findByRole("heading", { level: 1, name: "morning-digest" });
  expect(headline.querySelector("button")).toBeNull();

  const byline = screen.getByText("by");
  expect(byline.textContent).toBe("by assistant morning-digest");
  expect(within(byline).getByRole("link", { name: "assistant" }).getAttribute("href")).toBe(
    "#/agents/" + AGENT.id,
  );

  const preview = screen.getByRole("img", { name: "the queue" });
  expect(preview.getAttribute("src")).toBe("/dl/queue.png?preview");
  expect(preview.className).toContain("object-cover");
  expect(preview.className).toContain("object-top-left");
  expect(preview.closest("a")).toBeNull();
  expect(preview.closest("button")).toBeTruthy();
  expect(screen.getByText("brief.pdf").closest("a")).toBeNull();
  expect(screen.getByText("brief.pdf").closest("button")).toBeTruthy();
  expect(await screen.findByText("Two blockers cleared.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  expect(screen.queryByText("notes.md")).toBeNull();

  expect(screen.getByRole("link", { name: "Conversation" }).getAttribute("href")).toBe(
    "#/c/" + CONVO_ID,
  );
  const thread = screen.getByRole("link", { name: "Slack" });
  expect(thread.textContent).toBe("Slack ↗");
  expect(thread.getAttribute("href")).toBe(RUN.source);
  expect(screen.getByText(/ago$/).closest("a")?.getAttribute("href")).toBe(
    "#/radar?open=run%2F" + RUN.turn_id,
  );
});

test("a story that ended badly is marked and says why", async () => {
  wire({ "/workspace/radar": () => json({ runs: [FAILED_RUN], older: null }) });
  mountPinnedRun(FAILED_RUN);

  expect(await screen.findByRole("heading", { level: 1, name: "weekly-numbers" })).toBeTruthy();
  expect(screen.getByText("Failed")).toBeTruthy();
  expect(screen.getByText("The roll-up source timed out.")).toBeTruthy();
});

/** jsdom lays nothing out, so a document longer than the screen is stated rather than laid out —
 *  the measurement a fold would have read, kept so a report can be proven to stand whole without
 *  one. */
function laid(content: number, fold: number) {
  const held = [
    vi.spyOn(Element.prototype, "scrollHeight", "get").mockReturnValue(content),
    vi.spyOn(Element.prototype, "clientHeight", "get").mockReturnValue(fold),
  ];
  return () => held.forEach((spy) => spy.mockRestore());
}

test("a story is titled the way its report titles itself, and says that title once", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("# Standup\n\nTwo blockers cleared."),
  });
  mountPinnedRun(RUN);

  expect(await screen.findByRole("heading", { level: 1, name: "Standup" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "morning-digest" })).toBeNull();
  /* The page is headed by the report, so the document beneath must not repeat its own title. */
  expect(screen.getAllByText("Standup")).toHaveLength(1);
  expect(screen.getByText("Two blockers cleared.")).toBeTruthy();
  expect(screen.getByText("by").textContent).toBe("by assistant morning-digest");
});

const LONG_REPORT =
  "# Standup\n\n" + "Two blockers cleared. ".repeat(80) + "\n\nThe queue is empty.";

test("a permalinked report reads whole, with no fold and no box of its own that scrolls", async () => {
  const restore = laid(900, 280);
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response(LONG_REPORT),
  });
  mountPinnedRun(RUN);

  const story = (await screen.findByText("The queue is empty.")).closest("li");
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Show less" })).toBeNull();
  expect(story?.querySelector("[data-slot='reveal']")).toBeNull();
  expect(story?.innerHTML).not.toContain("max-h-(--size-reveal)");
  expect(story?.querySelector("[data-artifact-document]")).toBeNull();
  expect(story?.querySelector(".overflow-y-auto")).toBeNull();
  restore();
});

test("the feed reads the roll-up of every report, held to its own lines", async () => {
  const restore = laid(900, 280);
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response(LONG_REPORT),
  });
  mountRadarSection();

  const entry = (
    await screen.findByRole("heading", { level: 3, name: WRITTEN.title })
  ).closest("li") as HTMLElement;
  expect(within(entry).getByRole("heading", { level: 3 }).className).toContain("line-clamp-2");
  expect(within(entry).getByText(WRITTEN.summary).className).toContain("line-clamp-2");
  expect(screen.queryByText("The queue is empty.")).toBeNull();
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  restore();
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
  mountPinnedRun(RUN);

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));

  expect(await screen.findByRole("heading", { level: 2, name: "morning-digest" })).toBeTruthy();
  expect(await screen.findByText("check the queue")).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT.id);

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("heading", { level: 2, name: "morning-digest" })).toBeNull();
  expect(await screen.findByRole("heading", { level: 3, name: WRITTEN.title })).toBeTruthy();
});

test("a permalink pins the feed to its one report, and the way back is offered", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": (url) => {
      reads.push(url);
      return url.includes("turn=")
        ? json({ runs: [RUN], older: null })
        : json({ runs: [RUN, FAILED_RUN], older: null });
    },
    "/dl/notes.md": () => new Response("# Standup\n\nTwo blockers cleared."),
  });
  mountPinnedRun(RUN);

  expect(await screen.findByRole("heading", { level: 1, name: "Standup" })).toBeTruthy();
  expect(reads[0]).toContain("turn=" + RUN.turn_id);
  expect(screen.queryByText("The roll-up source timed out.")).toBeNull();
  /* The way out stands over the report as the path it was opened from, so the foot of a document
     the member came to read carries nothing to press. */
  expect(screen.queryByRole("button", { name: "All reports" })).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Back to Radar" }));
  expect(await screen.findByText("The roll-up source timed out.")).toBeTruthy();
  expect(reads.some((read) => !read.includes("turn="))).toBe(true);
  expect(screen.getByRole("heading", { level: 1, name: "Radar" })).toBeTruthy();
});

test("a permalink that resolves no readable run states it", async () => {
  wire({ "/workspace/radar": () => json({ runs: [], older: null }) });
  mountPinnedRun(RUN);

  expect(
    await screen.findByText("This report does not exist or is not shared with you."),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Back to Radar" })).toBeTruthy();
});

test("a shared picture opens full with its download", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("# Standup\n\nAll clear."),
  });
  mountPinnedRun(RUN);

  await userEvent.click(await screen.findByRole("img", { name: "the queue" }));

  const sheet = screen.getByRole("dialog");
  expect(within(sheet).getByText("queue.png")).toBeTruthy();
  const download = within(sheet).getByRole("link", { name: "Download" });
  expect(download.getAttribute("href")).toBe("/dl/queue.png");
  expect(download.getAttribute("download")).toBe("queue.png");
  const full = within(sheet).getByRole("img", { name: "the queue" });
  expect(full.getAttribute("src")).toBe("/dl/queue.png?preview");
  expect(full.className).toContain("object-contain");

  await userEvent.click(within(sheet).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("a shared file with no picture opens on its name with its download", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("# Standup\n\nAll clear."),
  });
  mountPinnedRun(RUN);

  const chip = await screen.findByText("brief.pdf");
  await userEvent.click(chip.closest("button") as HTMLElement);

  const sheet = screen.getByRole("dialog");
  expect(
    within(sheet).getByText("No preview for this file type. Download it to open it."),
  ).toBeTruthy();
  expect(within(sheet).getByRole("link", { name: "Download" }).getAttribute("href")).toBe(
    "/dl/brief.pdf",
  );
});
