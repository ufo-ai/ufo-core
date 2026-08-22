import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";
import { sectionHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  CONVO_ID,
  MEMBER,
  PlacedSection,
  SECOND,
  SECOND_ID,
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

/** A record's lane: the app whose namespace holds it, the kind and the name, in the one string the
 *  address and the store both carry. */
const DIGEST = "object/" + AGENT_ID + "/scheduled_task/morning-digest";
const ROLL = "object/" + SECOND_ID + "/scheduled_task/numbers-roll";

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
      <PlacedSection section="radar" place={{ opens: ["run/" + run.turn_id] }} />
    </MainAgentProvider>,
  );
}

/** The feed is the app's own front page, so the band over it states the app and nothing before
 *  it: a crumb there would name a place the member has not left. The band heads the pane rather
 *  than the words under it — above the scroller, at the pane's own width — so the name sits at the
 *  pane's left edge and stands still while the feed moves under it. It draws no rule: the words
 *  below it start at their own measure, which is the seam, and a line across the pane on top of
 *  that states the same boundary twice. The reading measure and the gutter are the content's
 *  alone. */
test("the feed is headed by the app's own name, in a band at the pane's own width", async () => {
  wire({ "/workspace/radar": () => json({ runs: [RUN], older: null }) });
  mountRadarSection();

  const named = await screen.findByRole("heading", { level: 1, name: "Radar" });
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();

  const band = named.closest("[data-slot=header]") as HTMLElement;
  expect(band.className).not.toContain("border-b");
  expect(band.closest(".overflow-y-auto")).toBeNull();
  expect(band.closest(".max-w-page")).toBeNull();
});

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

  const headline = await screen.findByRole("heading", { level: 3, name: "morning-digest" });
  expect(headline.querySelector("button")).toBeNull();
  expect(screen.getByRole("navigation", { name: "Breadcrumb" }).textContent).toBe(
    "Radar/morning-digest",
  );

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

  expect(await screen.findByRole("heading", { level: 3, name: "weekly-numbers" })).toBeTruthy();
  expect(screen.getByRole("navigation", { name: "Breadcrumb" }).textContent).toBe(
    "Radar/weekly-numbers",
  );
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

/** The band and the body say the same name on purpose: the band gives it one line and the measure
 *  the crumb leaves, and a report titles itself in prose that will not fit there. The body is where
 *  the member reads it whole. Only one of the two is a heading of the page — the crumb's leaf — so
 *  the name is stated twice and headed once. */
test("a story is titled the way its report titles itself, in the band and whole in the body", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("# Standup\n\nTwo blockers cleared."),
  });
  mountPinnedRun(RUN);

  expect(await screen.findByRole("heading", { level: 3, name: "Standup" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "morning-digest" })).toBeNull();
  const path = screen.getByRole("navigation", { name: "Breadcrumb" });
  await waitFor(() => expect(path.textContent).toBe("Radar/Standup"));
  const headed = screen.getAllByRole("heading", { level: 1 });
  expect(headed).toHaveLength(1);
  expect(path.contains(headed[0])).toBe(true);
  /* Twice on the screen — the crumb and the body — and no third: the document drops the title
     line the story already stands under. */
  expect(screen.getAllByText("Standup")).toHaveLength(2);
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

/** The record a task name opens, as the surface states it. */
function task(name: string, prompt: string, links: unknown[] = []) {
  return json({
    ...TASK_KIND,
    name,
    summary: "0 9 * * * — " + prompt,
    spec: { schedule: "0 9 * * *", prompt, paused: false },
    status: { next_run_at: "2026-08-15T09:00:00+00:00", paused: false },
    links,
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-08-01T09:00:00Z",
  });
}

/** What `morning-digest` links out to, which is how a record opens the next one after itself. */
const LINKED = "follows scheduled task weekly-numbers";

function wireTasks() {
  return wire({
    "/workspace/radar": () => json({ runs: [RUN, FAILED_RUN], older: null }),
    "/objects/scheduled_task/morning-digest": () =>
      task("morning-digest", "check the queue", [
        { relation: "follows", kind: "scheduled_task", name: "weekly-numbers", opens: true },
      ]),
    "/objects/scheduled_task/weekly-numbers": () => task("weekly-numbers", "roll up the numbers"),
  });
}

const named = (name: string) => screen.getByRole("button", { name });

const standing = () =>
  screen.queryAllByRole("region").map((slot) => slot.getAttribute("aria-label"));

test("a story's task opens the task record where the prompt is read", async () => {
  const reads: string[] = [];
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("All quiet on the queue."),
    "/objects/scheduled_task/morning-digest": (url) => {
      reads.push(url);
      return task("morning-digest", "check the queue");
    },
  });
  mountPinnedRun(RUN);

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));

  expect(await screen.findByRole("region", { name: "morning-digest" })).toBeTruthy();
  expect(await screen.findByText("check the queue")).toBeTruthy();
  expect(reads[0]).toContain("agent=" + AGENT.id);

  await userEvent.click(screen.getByRole("button", { name: "Close morning-digest" }));
  expect(standing()).toEqual([]);
  expect(screen.getByRole("heading", { level: 3, name: "morning-digest" })).toBeTruthy();
});

/** A lane states the surface it was opened out of, which is the way back a member reading one lane
 *  to a screen has instead of the feed standing beside it. */
test("a task's record says the feed it was opened from, and the crumb shuts it", async () => {
  wireTasks();
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));

  const lane = await screen.findByRole("region", { name: "morning-digest" });
  const path = within(lane).getByRole("navigation", { name: "Breadcrumb" });
  expect(path.textContent).toBe("Radar/morning-digest");

  await userEvent.click(within(path).getByRole("button", { name: "Back to Radar" }));

  expect(standing()).toEqual([]);
});

/** The feed is one list and the record beside it is where the member is reading, so a second task
 *  name is a step taken from the feed rather than a second thing left lying open. */
test("a second task name takes the first record's place", async () => {
  wireTasks();
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await screen.findByRole("region", { name: "morning-digest" });
  await userEvent.click(named("weekly-numbers"));

  expect(await screen.findByRole("region", { name: "weekly-numbers" })).toBeTruthy();
  expect(standing()).toEqual(["weekly-numbers"]);
  expect(screen.queryByText("check the queue")).toBeNull();
  expect(screen.getByRole("heading", { level: 3, name: WRITTEN.title })).toBeTruthy();
});

/** Two tasks side by side is what the member asks for with the gesture a browser already opens a
 *  link in its own place with, and it truncates nothing. */
test("a cmd-press and a middle-press stand a task beside what is open", async () => {
  wireTasks();
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await screen.findByRole("region", { name: "morning-digest" });

  fireEvent.click(named("weekly-numbers"), { metaKey: true });

  expect(await screen.findByRole("region", { name: "weekly-numbers" })).toBeTruthy();
  expect(standing()).toEqual(["morning-digest", "weekly-numbers"]);
  expect(screen.getByText("check the queue")).toBeTruthy();
  expect(await screen.findByText("roll up the numbers")).toBeTruthy();

  await userEvent.click(named("Close weekly-numbers"));
  fireEvent(named("weekly-numbers"), new MouseEvent("auxclick", { bubbles: true, button: 1 }));

  expect(await screen.findByRole("region", { name: "weekly-numbers" })).toBeTruthy();
  expect(standing()).toEqual(["morning-digest", "weekly-numbers"]);
});

test("pressing the task name whose record is standing changes nothing", async () => {
  const { calls } = wireTasks();
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await screen.findByRole("region", { name: "morning-digest" });
  const read = calls.filter((url) => url.includes("/scheduled_task/morning-digest")).length;

  await userEvent.click(named("morning-digest"));

  expect(standing()).toEqual(["morning-digest"]);
  expect(screen.getAllByText("check the queue")).toHaveLength(1);
  expect(calls.filter((url) => url.includes("/scheduled_task/morning-digest"))).toHaveLength(read);
});

/** A record reached through another is only there because of it, so shutting the one shuts the
 *  other with it. */
test("a link inside a record opens after it, and closing that record shuts both", async () => {
  wireTasks();
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await userEvent.click(await screen.findByRole("button", { name: LINKED }));

  expect(await screen.findByRole("region", { name: "weekly-numbers" })).toBeTruthy();
  expect(standing()).toEqual(["morning-digest", "weekly-numbers"]);

  await userEvent.click(named("Close morning-digest"));

  expect(standing()).toEqual([]);
  expect(screen.queryByText("roll up the numbers")).toBeNull();
});

/** A link a member was sent carries the track whole, and the screen may not be able to draw what
 *  it names: an id that is no object at all. The lane stands anyway and says so, because the close
 *  belongs to the lane — drawing nothing leaves the address holding a record the member can neither
 *  read nor get rid of. */
test("an open id the screen cannot draw stands as a lane that says so and closes", async () => {
  wireTasks();
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="radar" place={{ opens: [DIGEST, "nonsense/1"] }} />
    </MainAgentProvider>,
  );

  await screen.findByRole("heading", { level: 3, name: WRITTEN.title });
  expect(standing()).toEqual(["morning-digest", "nonsense/1"]);
  expect(screen.getAllByText("That item is not on this page.")).toHaveLength(1);
  expect(await screen.findByText("check the queue")).toBeTruthy();

  await userEvent.click(named("Close nonsense/1"));
  expect(standing()).toEqual(["morning-digest"]);

  await userEvent.click(named("Close morning-digest"));
  expect(standing()).toEqual([]);
});

/** The feed crosses apps, so a record lane names the app it is read in. The sidebar names the app
 *  and nothing on it, and a member coming back that way has to find the record they left standing
 *  rather than a lane saying it is not on this page. */
test("a task record left standing comes back in the app it was read in", async () => {
  location.hash = sectionHash("radar");
  wireTasks();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  const opened = sectionHash("radar", { opens: [DIGEST] });
  await waitFor(() => expect(location.hash).toBe(opened));
  expect(await screen.findByText("check the queue")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await waitFor(() => expect(location.hash).toBe("#/new/" + AGENT.id));

  await userEvent.click(screen.getByRole("button", { name: "Radar" }));

  await waitFor(() => expect(location.hash).toBe(opened));
  expect(await screen.findByRole("region", { name: "morning-digest" })).toBeTruthy();
  expect(await screen.findByText("check the queue")).toBeTruthy();
  expect(screen.queryByText("That item is not on this page.")).toBeNull();
});

/** Finder marks the row it opened in every column, which is what makes the column to its right
 *  read as where the member is rather than as a panel that appeared. */
test("the feed row whose record is standing is marked", async () => {
  wireTasks();
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await screen.findByRole("region", { name: "morning-digest" });

  expect(named("morning-digest").getAttribute("aria-current")).toBe("true");
  expect(named("weekly-numbers").getAttribute("aria-current")).toBe("false");

  await userEvent.click(named("weekly-numbers"));
  await screen.findByRole("region", { name: "weekly-numbers" });

  expect(named("morning-digest").getAttribute("aria-current")).toBe("false");
  expect(named("weekly-numbers").getAttribute("aria-current")).toBe("true");
});

/** A pinned run is what the feed is standing on, not a record standing beside it, so a task opened
 *  from that story opens after the pin and leaves it pinned — and the way back off the pin takes
 *  what was opened from it. */
test("a task opened from a pinned story leaves the story pinned", async () => {
  wire({
    "/workspace/radar": (url) =>
      url.includes("turn=")
        ? json({ runs: [RUN], older: null })
        : json({ runs: [RUN, FAILED_RUN], older: null }),
    "/dl/notes.md": () => new Response("All quiet on the queue."),
    "/objects/scheduled_task/morning-digest": () => task("morning-digest", "check the queue"),
  });
  mountPinnedRun(RUN);

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));

  expect(await screen.findByRole("region", { name: "morning-digest" })).toBeTruthy();
  expect(standing()).toEqual(["morning-digest"]);
  expect(screen.queryByText("The roll-up source timed out.")).toBeNull();

  await userEvent.click(named("Back to Radar"));

  expect(await screen.findByText("The roll-up source timed out.")).toBeTruthy();
  expect(standing()).toEqual([]);
});

/** A record on this screen opens beside the feed; a link to another screen is still a link. The
 *  boundary is what the member reaches, not what the control looks like: the agent and the
 *  conversation are elsewhere, and pressing them leaves the radar. */
test("a link to another screen navigates instead of opening a slot", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/dl/notes.md": () => new Response("All quiet on the queue."),
  });
  mountPinnedRun(RUN);

  const app = await screen.findByRole("link", { name: "assistant" });
  expect(app.getAttribute("href")).toBe("#/agents/" + AGENT.id);

  const conversation = screen.getByRole("link", { name: "Conversation" });
  expect(conversation.getAttribute("href")).toBe("#/c/" + CONVO_ID);

  await userEvent.click(conversation);
  expect(location.hash).toBe("#/c/" + CONVO_ID);
  expect(standing()).toEqual([]);
});

/** The band over a pinned page is the whole of the way back: the app's own name, the report under
 *  it, and nothing else on the screen saying either. */
test("a permalink pins the feed to its one report, and the crumb is the way back", async () => {
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
  const path = screen.getByRole("navigation", { name: "Breadcrumb" });
  await waitFor(() => expect(path.textContent).toBe("Radar/Standup"));

  await userEvent.click(within(path).getByRole("button", { name: "Back to Radar" }));

  expect(await screen.findByText("The roll-up source timed out.")).toBeTruthy();
  expect(reads.at(-1)).not.toContain("turn=");
  expect(screen.getByRole("heading", { level: 1, name: "Radar" })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
});

/** A member who read to the end of a report is deciding what to read next, not whether to go back:
 *  the way back stands in the crumb at the top, and the foot offers the reports either side of this
 *  one, named the way the feed names them. Each is a link to its own address, and that address is
 *  the whole track — the report the member has just left does not stay standing beside the next. */
test("the foot of a report offers what to read next, not a way back", async () => {
  wire({
    "/workspace/radar": (url) =>
      url.includes("turn=")
        ? json({ runs: [RUN], older: null })
        : json({ runs: [RUN, FAILED_RUN, QUIET_RUN], older: null }),
    "/dl/notes.md": () => new Response("All quiet on the queue."),
  });
  mountPinnedRun(RUN);

  const foot = (await screen.findByRole("heading", { level: 2, name: "More reports" }))
    .closest("section") as HTMLElement;
  const offered = within(foot).getAllByRole("link");

  expect(offered.map((row) => row.textContent)).toEqual(["weekly-numbers ", "quiet-check "]);
  expect(offered[0].getAttribute("href")).toBe("#/radar?open=run%2F" + FAILED_RUN.turn_id);
  expect(within(foot).queryByText(WRITTEN.title)).toBeNull();
  expect(within(foot).queryByRole("button")).toBeNull();
});

/** A run that answers nothing names itself as what the member came for, because the crumb over it
 *  is the only way off the page and it cannot wait on a name that is never coming. */
test("a permalink that resolves no readable run states it", async () => {
  wire({ "/workspace/radar": () => json({ runs: [], older: null }) });
  mountPinnedRun(RUN);

  expect(
    await screen.findByText("This report does not exist or is not shared with you."),
  ).toBeTruthy();
  expect(screen.getByRole("navigation", { name: "Breadcrumb" }).textContent).toBe("Radar/Report");
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

const ENTRIES_QUEUED =
  "The last seven days' entries are written again — 4 reports. Each stands on its task's name " +
  "until the digest job reaches it.";

test("the feed states the window a rebuild reaches before it is pressed", async () => {
  wire({ "/workspace/radar": () => json({ runs: [RUN], older: null }) });
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "Rebuild entries" }));
  await screen.findByRole("heading", { name: "Rebuild Entries" });
  expect(screen.getByText(/last seven days is read again/)).toBeTruthy();
  expect(screen.getByText(/older than seven days keeps the entry it has/)).toBeTruthy();
});

test("a rebuild rides the main agent's intent lane and states what it queued", async () => {
  const posted: unknown[] = [];
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: ENTRIES_QUEUED });
    },
  });
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "Rebuild entries" }));
  const dialog = screen.getByRole("dialog");
  await userEvent.click(within(dialog).getByRole("button", { name: "Rebuild entries" }));

  await waitFor(() => expect(posted).toEqual([{ verb: "rebuild_reports" }]));
  expect(await screen.findByText(ENTRIES_QUEUED)).toBeTruthy();
  expect(within(dialog).queryByRole("button", { name: "Rebuild entries" })).toBeNull();
});

test("a refused rebuild is read back in the dialog, and the act stands", async () => {
  wire({
    "/workspace/radar": () => json({ runs: [RUN], older: null }),
    "/intents": () =>
      json({ applied: false, message: "Only a workspace admin can write the radar entries again." }),
  });
  mountRadarSection();

  await userEvent.click(await screen.findByRole("button", { name: "Rebuild entries" }));
  const dialog = screen.getByRole("dialog");
  await userEvent.click(within(dialog).getByRole("button", { name: "Rebuild entries" }));

  const notice = await screen.findByText(
    "Only a workspace admin can write the radar entries again.",
  );
  expect(notice.className).toContain("bg-attention");
  expect(within(dialog).getByRole("button", { name: "Rebuild entries" })).toBeTruthy();
});

/** A run of a second app's own task. The feed spans every app the member reaches, so two entries
 *  standing next to each other are routinely two apps', and the records they open live in two
 *  namespaces. */
const SECOND_RUN = {
  ...RUN,
  turn_id: "4f2c6b87-9eca-4d53-b071-c297e46f1a59",
  agent_id: SECOND.id,
  task: "numbers-roll",
  fired_at: "2026-08-14T08:30:00+00:00",
  text: "",
  entry: null,
  artifacts: [],
};

function wireTwoApps() {
  return wire({
    "/workspace/radar": () => json({ runs: [RUN, SECOND_RUN], older: null }),
    "/objects/scheduled_task/morning-digest": () => task("morning-digest", "check the queue"),
    "/objects/scheduled_task/numbers-roll": () => task("numbers-roll", "roll up the numbers"),
    "/transcript": () => json({ messages: [] }),
  });
}

const readsOf = (calls: string[], name: string) =>
  calls.filter((url) => url.includes("/scheduled_task/" + name));

/** The lane carries the app it is read in, so standing a second app's record beside the first
 *  leaves the first where it was. A track holding one owner for every lane repointed the record
 *  already standing the moment the second one opened, and the member read one app's task under
 *  another app's name. */
test("a second app's task stands beside the first, and the first is not repointed", async () => {
  const { calls } = wireTwoApps();
  render(
    <MainAgentProvider agents={[AGENT, SECOND]}>
      <PlacedSection section="radar" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await screen.findByRole("region", { name: "morning-digest" });

  fireEvent.click(named("numbers-roll"), { metaKey: true });

  expect(await screen.findByRole("region", { name: "numbers-roll" })).toBeTruthy();
  expect(standing()).toEqual(["morning-digest", "numbers-roll"]);
  expect(await screen.findByText("check the queue")).toBeTruthy();
  expect(await screen.findByText("roll up the numbers")).toBeTruthy();

  const digest = readsOf(calls, "morning-digest");
  const roll = readsOf(calls, "numbers-roll");
  expect(digest.length).toBeGreaterThan(0);
  expect(roll.length).toBeGreaterThan(0);
  expect(digest.every((url) => url.includes("agent=" + AGENT.id))).toBe(true);
  expect(roll.every((url) => url.includes("agent=" + SECOND_ID))).toBe(true);
});

/** The store holds the lanes, and each lane holds the app it is read in, so a screen the member
 *  came back to by name draws the same two records under the same two apps. */
test("a track of two apps' records comes back off the store in both apps", async () => {
  location.hash = sectionHash("radar");
  const { calls } = wireTwoApps();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "morning-digest" }));
  await screen.findByRole("region", { name: "morning-digest" });
  fireEvent.click(named("numbers-roll"), { metaKey: true });
  await screen.findByRole("region", { name: "numbers-roll" });
  const opened = sectionHash("radar", { opens: [DIGEST, ROLL] });
  await waitFor(() => expect(location.hash).toBe(opened));

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await waitFor(() => expect(location.hash).toBe("#/new/" + AGENT.id));
  calls.length = 0;

  await userEvent.click(screen.getByRole("button", { name: "Radar" }));

  await waitFor(() => expect(location.hash).toBe(opened));
  expect(await screen.findByRole("region", { name: "morning-digest" })).toBeTruthy();
  expect(await screen.findByRole("region", { name: "numbers-roll" })).toBeTruthy();
  expect(screen.queryByText("That item is not on this page.")).toBeNull();
  const digest = readsOf(calls, "morning-digest");
  const roll = readsOf(calls, "numbers-roll");
  expect(digest.length).toBeGreaterThan(0);
  expect(roll.length).toBeGreaterThan(0);
  expect(digest.every((url) => url.includes("agent=" + AGENT.id))).toBe(true);
  expect(roll.every((url) => url.includes("agent=" + SECOND_ID))).toBe(true);
});

/** A link states its whole track, lane by lane, and each lane states the app its record is read
 *  in — so a member sent a link to two apps' records reads both, and the address they land on is
 *  the one they were sent. */
test("a link carrying two apps' records round trips through the address", async () => {
  const { calls } = wireTwoApps();
  const link = sectionHash("radar", { opens: [DIGEST, ROLL] });
  location.hash = link;
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("check the queue")).toBeTruthy();
  expect(await screen.findByText("roll up the numbers")).toBeTruthy();
  expect(standing()).toEqual(["morning-digest", "numbers-roll"]);
  expect(screen.queryByText("That item is not on this page.")).toBeNull();
  expect(location.hash).toBe(link);
  expect(readsOf(calls, "morning-digest").every((url) => url.includes("agent=" + AGENT.id))).toBe(
    true,
  );
  expect(readsOf(calls, "numbers-roll").every((url) => url.includes("agent=" + SECOND_ID))).toBe(
    true,
  );
});
