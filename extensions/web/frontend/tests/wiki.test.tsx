import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";
import { parseHash, sectionHash, type WorkspacePlace } from "@/lib/route";
import { TRACK_MAX_SLOTS } from "@/lib/tracks";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS } from "@/views/registry";

import { AGENT, MEMBER, PlacedSection, json, useStreamFake, wire } from "./harness";

/** The kind memory items are filed under, as the surface states it. A memory is written in chat and
 *  read here, so the lane takes neither an apply nor a delete for it. */
const MEMORY_KIND = {
  kind: "memory",
  fields: ["memory_kind", "subject", "written"],
  spec_schema: { properties: { text: { type: "string", title: "Text" } } },
  applies: false,
  deletes: false,
};

const ROSTER = [
  { name: "m1", email: "member@example.com", admin: true, seated: true },
];

/** One item under a topic: the bullet the page draws, and the record that bullet opens. */
function item(name: string, summary: string) {
  return {
    name,
    summary,
    subject: "shared",
    memory_kind: "decision",
    written: "2026-08-10T09:00:00+00:00",
    agent_id: AGENT.id,
  };
}

function record(name: string, summary: string, text: string, links: unknown[] = []) {
  return json({
    ...MEMORY_KIND,
    name,
    summary,
    spec: { text },
    status: { written: "2026-08-10T09:00:00+00:00" },
    links,
    created_at: "2026-08-10T09:00:00Z",
    updated_at: "2026-08-10T09:00:00Z",
  });
}

const DECIDED = item("ship-friday", "Releases go out on Friday.");
/** The bullet's lane: the app that filed the memory, the kind and the name, in the one string the
 *  address and the store both carry. */
const DECIDED_LANE = "object/" + AGENT.id + "/memory/ship-friday";
const PAUSED = item("hold-migrations", "Migrations wait for the freeze to lift.");
const OWED = item("owe-the-quarter", "The quarter's numbers are owed on the 30th.");

/** What `ship-friday` links out to, which is how a record opens the next one after itself. */
const LINKED = "notes memory hold-migrations";

/** What the tool answers a queued rebuild with, which the dialog reads back verbatim. */
const QUEUED =
  "The facts derived from synced pages are written again as the derivation pass reaches each " +
  "page. Overview summaries and items an app recorded in a conversation are untouched.";

const SUMMARIES = () =>
  json({ available: true, kinds: ["semantic"], matches: [], body_max_chars: 115 });

function page() {
  return {
    "/workspace/memory": SUMMARIES,
    "/objects/member": () => json({ objects: ROSTER }),
    "/objects/memory/ship-friday": () =>
      record("ship-friday", DECIDED.summary, "Only the smoke run gates it.", [
        { relation: "notes", kind: "memory", name: "hold-migrations", opens: true },
      ]),
    "/objects/memory/hold-migrations": () =>
      record("hold-migrations", PAUSED.summary, "The freeze lifts on the 20th."),
    "/objects/memory/owe-the-quarter": () =>
      record("owe-the-quarter", OWED.summary, "Finance closes the books first."),
    "/objects/memory?": (url: string) =>
      json({
        objects: url.includes("memory_kind=decision") ? [DECIDED, PAUSED, OWED] : [],
      }),
  };
}

function wireWiki() {
  return wire(page());
}

function mountWiki() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="wiki" />
    </MainAgentProvider>,
  );
}

const standing = () =>
  screen.queryAllByRole("region").map((slot) => slot.getAttribute("aria-label"));

const bullet = (summary: string) => screen.getByRole("button", { name: summary });

async function openTheDialog() {
  await userEvent.click(await screen.findByRole("button", { name: "Page actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Rebuild page facts" }));
}

beforeEach(() => {
  useStreamFake();
});

test("a topic bullet opens the item's record in a slot beside the page", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));

  expect(await screen.findByRole("region", { name: "ship-friday" })).toBeTruthy();
  expect(await screen.findByText("Only the smoke run gates it.")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 1, name: "Wiki" })).toBeTruthy();
  expect(screen.getByRole("button", { name: PAUSED.summary })).toBeTruthy();
});

/** The page is one column of a path, not a workbench: a second bullet is a step taken from the
 *  same place as the first, so it stands where the first record stood. */
test("a second bullet takes the first record's place", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await screen.findByRole("region", { name: "ship-friday" });
  await userEvent.click(bullet(PAUSED.summary));

  expect(await screen.findByRole("region", { name: "hold-migrations" })).toBeTruthy();
  expect(standing()).toEqual(["hold-migrations"]);
  expect(screen.queryByText("Only the smoke run gates it.")).toBeNull();
});

/** Two records side by side is what the member asks for with the gesture a browser already opens a
 *  link in its own place with, and it truncates nothing. */
test("a cmd-press and a middle-press stand records beside what is open", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await screen.findByRole("region", { name: "ship-friday" });

  fireEvent.click(bullet(PAUSED.summary), { metaKey: true });
  expect(await screen.findByRole("region", { name: "hold-migrations" })).toBeTruthy();

  fireEvent(bullet(OWED.summary), new MouseEvent("auxclick", { bubbles: true, button: 1 }));
  expect(await screen.findByRole("region", { name: "owe-the-quarter" })).toBeTruthy();

  expect(standing()).toEqual(["ship-friday", "hold-migrations", "owe-the-quarter"]);
});

test("pressing the bullet whose record is standing changes nothing", async () => {
  const { calls } = wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await screen.findByRole("region", { name: "ship-friday" });
  const read = calls.filter((url) => url.includes("/objects/memory/ship-friday")).length;

  await userEvent.click(bullet(DECIDED.summary));

  expect(standing()).toEqual(["ship-friday"]);
  expect(screen.getAllByText("Only the smoke run gates it.")).toHaveLength(1);
  expect(calls.filter((url) => url.includes("/objects/memory/ship-friday"))).toHaveLength(read);
});

/** The address is what the member sends and what Back walks, so a press that changes nothing
 *  leaves nothing behind for Back to walk through. */
test("pressing the standing bullet again leaves no entry for Back to walk", async () => {
  location.hash = sectionHash("wiki");
  wireWiki();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  const opened = sectionHash("wiki", { opens: [DECIDED_LANE] });
  await waitFor(() => expect(location.hash).toBe(opened));

  await userEvent.click(bullet(DECIDED.summary));
  expect(location.hash).toBe(opened);

  history.back();

  await waitFor(() => expect(location.hash).toBe(sectionHash("wiki")));
});

/** A memory is read in the namespace of the app that filed it, and its lane names that app. The
 *  sidebar names the app and nothing on it, and the record the member left standing has to come
 *  back readable rather than as a lane the page drops. */
test("a memory record left standing comes back in the app it was read in", async () => {
  location.hash = sectionHash("wiki");
  wireWiki();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  const opened = sectionHash("wiki", { opens: [DECIDED_LANE] });
  await waitFor(() => expect(location.hash).toBe(opened));
  expect(await screen.findByText("Only the smoke run gates it.")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "New conversation" }));
  await waitFor(() => expect(location.hash).toBe("#/new/" + AGENT.id));

  await userEvent.click(screen.getByRole("button", { name: "Wiki" }));

  await waitFor(() => expect(location.hash).toBe(opened));
  expect(await screen.findByRole("region", { name: "ship-friday" })).toBeTruthy();
  expect(await screen.findByText("Only the smoke run gates it.")).toBeTruthy();
});

/** A record reached through another is only there because of it, so shutting the one shuts the
 *  other with it. */
test("a link inside a record opens after it, and closing that record shuts both", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await userEvent.click(await screen.findByRole("button", { name: LINKED }));

  expect(await screen.findByRole("region", { name: "hold-migrations" })).toBeTruthy();
  expect(standing()).toEqual(["ship-friday", "hold-migrations"]);

  await userEvent.click(screen.getByRole("button", { name: "Close ship-friday" }));

  expect(standing()).toEqual([]);
  expect(screen.queryByText("The freeze lifts on the 20th.")).toBeNull();
});

test("closing the newest record leaves the one before it standing", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await screen.findByRole("region", { name: "ship-friday" });
  fireEvent.click(bullet(PAUSED.summary), { metaKey: true });
  await screen.findByRole("region", { name: "hold-migrations" });

  await userEvent.click(screen.getByRole("button", { name: "Close hold-migrations" }));

  expect(standing()).toEqual(["ship-friday"]);
  expect(screen.getByText("Only the smoke run gates it.")).toBeTruthy();
});

/** Finder marks the row it opened in every column, which is what makes the column to its right
 *  read as where the member is rather than as a panel that appeared. */
test("the bullet whose record is standing is marked", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await screen.findByRole("region", { name: "ship-friday" });

  expect(bullet(DECIDED.summary).getAttribute("aria-current")).toBe("true");
  expect(bullet(PAUSED.summary).getAttribute("aria-current")).toBe("false");

  await userEvent.click(bullet(PAUSED.summary));
  await screen.findByRole("region", { name: "hold-migrations" });

  expect(bullet(DECIDED.summary).getAttribute("aria-current")).toBe("false");
  expect(bullet(PAUSED.summary).getAttribute("aria-current")).toBe("true");
});

/** A member's page is what the pane's own body draws, not a lane beside it, so opening one is
 *  taking the page somewhere else — and the records opened from the page left behind go with it. */
test("a member's page takes the place of the workspace and the records opened from it", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));
  await screen.findByRole("region", { name: "ship-friday" });

  await userEvent.click(screen.getByText("member@example.com"));

  expect(await screen.findByRole("button", { name: "Back to Wiki" })).toBeTruthy();
  expect(standing()).toEqual([]);

  await userEvent.click(screen.getByRole("button", { name: "Back to Wiki" }));

  expect(await screen.findByRole("button", { name: DECIDED.summary })).toBeTruthy();
  expect(standing()).toEqual([]);
});

/** Where the member is, in the one band every screen is headed by: the app's own name on its front
 *  page, and the person under it on theirs — the crumb being what says so, the way back, and the
 *  page's one heading at once. A page whose name is only a crumb has no heading a reader can reach
 *  it by, and this page's name arrives with its own read, so nothing above it can state one. */
test("a member's page is headed by the app and their own name", async () => {
  wireWiki();
  mountWiki();

  expect(await screen.findByRole("heading", { level: 1, name: "Wiki" })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();

  await userEvent.click(screen.getByText("member@example.com"));

  const path = await screen.findByRole("navigation", { name: "Breadcrumb" });
  expect(path.textContent).toBe("Wiki/member@example.com");
  const head = screen.getAllByRole("heading", { level: 1 });
  expect(head.map((one) => one.textContent)).toEqual(["member@example.com"]);
  expect(path.contains(head[0])).toBe(true);

  await userEvent.click(within(path).getByRole("button", { name: "Back to Wiki" }));

  expect(await screen.findByRole("heading", { level: 1, name: "Wiki" })).toBeTruthy();
  expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
});

/** A lane states the surface it was opened out of, which is the way back a member reading one lane
 *  to a screen has instead of the page standing beside it. */
test("a record's lane says the page it was opened from, and the crumb shuts it", async () => {
  wireWiki();
  mountWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECIDED.summary }));

  const lane = await screen.findByRole("region", { name: "ship-friday" });
  const path = within(lane).getByRole("navigation", { name: "Breadcrumb" });
  expect(path.textContent).toBe("Wiki/ship-friday");

  await userEvent.click(within(path).getByRole("button", { name: "Back to Wiki" }));

  expect(standing()).toEqual([]);
});

/** The article is what the member came for, so it is the first thing they meet: the contents stand
 *  after it in the document as well as to its right, which is the order a keyboard and a screen
 *  reader take the page in. */
test("the contents rail stands after the article and anchors to its sections", async () => {
  wireWiki();
  mountWiki();

  const rail = await screen.findByRole("navigation", { name: "Contents" });
  const row = rail.parentElement!;
  const article = row.firstElementChild as HTMLElement;

  expect(row.lastElementChild).toBe(rail);
  expect(row.className).not.toContain("flex-row-reverse");
  expect(article.contains(screen.getByRole("heading", { name: "Decisions" }))).toBe(true);
  expect(rail.className).toContain("sticky");
  expect(rail.className).toContain("max-narrow:hidden");

  const anchored: Element[] = [];
  const into = vi
    .spyOn(Element.prototype, "scrollIntoView")
    .mockImplementation(function (this: Element) {
      anchored.push(this);
    });
  await userEvent.click(within(rail).getByRole("button", { name: "Decisions" }));
  into.mockRestore();

  expect(anchored).toEqual([document.getElementById("decision")]);
});

/** The article starts where the page body starts. The wiki holds its prose to a reading measure and
 *  nothing else: no column of its own centres it, so it runs from the page's own edge with the
 *  contents beyond it rather than standing in the middle of the pane. */
test("nothing the wiki draws insets the article from the page body's edge", async () => {
  wireWiki();
  mountWiki();

  const rail = await screen.findByRole("navigation", { name: "Contents" });
  const article = rail.parentElement!.firstElementChild as HTMLElement;

  const drawn: string[] = [];
  for (
    let held: HTMLElement | null = article;
    held !== null && held.dataset.testid === undefined;
    held = held.parentElement
  ) {
    drawn.push(held.className);
  }

  const centred = /\b(mx-auto|ms-auto|justify-center|max-w-page)\b/;
  expect(drawn.length).toBeGreaterThan(1);
  expect(drawn.filter((held) => centred.test(held))).toEqual([]);
});

test("the page states what a rebuild leaves alone before it is pressed", async () => {
  wireWiki();
  mountWiki();
  await openTheDialog();

  await screen.findByRole("heading", { name: "Rebuild Page Facts" });
  expect(screen.getByText(/derived from synced pages are written again/)).toBeTruthy();
  expect(screen.getByText(/Overview summaries are not rebuilt/)).toBeTruthy();
  expect(screen.getByText(/recorded in a conversation are not rebuilt/)).toBeTruthy();
});

test("the rebuild rides the main agent's intent lane and states what it queued", async () => {
  const posted: unknown[] = [];
  wire({
    ...page(),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: QUEUED });
    },
  });
  mountWiki();
  await openTheDialog();
  await userEvent.click(screen.getByRole("button", { name: "Rebuild page facts" }));

  await waitFor(() => expect(posted).toEqual([{ verb: "rebuild_page_facts" }]));
  expect(await screen.findByText(QUEUED)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rebuild page facts" })).toBeNull();
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
});

test("a refused rebuild is read back in place, and the act stands", async () => {
  wire({
    ...page(),
    "/intents": () =>
      json({
        applied: false,
        message: "Only a workspace admin can rebuild the facts derived from synced pages.",
      }),
  });
  mountWiki();
  await openTheDialog();
  await userEvent.click(screen.getByRole("button", { name: "Rebuild page facts" }));

  const notice = await screen.findByText(
    "Only a workspace admin can rebuild the facts derived from synced pages.",
  );
  expect(notice.className).toContain("bg-attention");
  expect(screen.getByRole("button", { name: "Rebuild page facts" })).toBeTruthy();
});

test("reloading the page re-reads it and admits no turn", async () => {
  let reads = 0;
  const { calls } = wire({
    ...page(),
    "/workspace/memory": () => {
      reads += 1;
      return SUMMARIES();
    },
  });
  mountWiki();
  await waitFor(() => expect(reads).toBe(1));

  await userEvent.click(await screen.findByRole("button", { name: "Page actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Reload" }));

  await waitFor(() => expect(reads).toBe(2));
  expect(calls.some((url) => url.includes("/intents"))).toBe(false);
});

/** One topic's worth of bullets, named so a press can pick any of them out by its own words. */
const filed = (kind: string, count: number, from = 0) =>
  Array.from({ length: count }, (_, at) => ({
    ...item(kind + "-" + (at + from), "Item " + kind + " " + (at + from) + "."),
    memory_kind: kind,
  }));

const DECISIONS = filed("decision", TRACK_MAX_SLOTS);
const PREFERENCES = filed("preference", 1);

function wireFull() {
  return wire({
    "/workspace/memory": SUMMARIES,
    "/objects/member": () => json({ objects: ROSTER }),
    "/objects/memory/": (url: string) => {
      const name = url.slice(url.lastIndexOf("/") + 1).split("?")[0];
      return record(name, "Item " + name + ".", "The body of " + name + ".");
    },
    "/objects/memory?": (url: string) =>
      json({
        objects: url.includes("memory_kind=decision")
          ? DECISIONS
          : url.includes("memory_kind=preference")
            ? PREFERENCES
            : [],
      }),
  });
}

/** The screen with the address it writes kept beside it, so a press can be read back as the link a
 *  member would be handed. */
function trackedWiki() {
  const places: WorkspacePlace[] = [];
  function Screen() {
    const [place, setPlace] = useState<WorkspacePlace>({});
    return (
      <TabbedPane
        group="section"
        tabs={["wiki"] as const}
        views={SECTION_VIEWS}
        view="wiki"
        place={place}
        onPlace={(_view, next) => {
          places.push(next);
          setPlace(next);
        }}
      />
    );
  }
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Screen />
    </MainAgentProvider>,
  );
  return {
    place: () => places.at(-1) ?? {},
    address: () => sectionHash("wiki", places.at(-1) ?? {}),
  };
}

/** The press past the cap is the one the address cannot carry, so it is the one that has to do
 *  nothing: a track written longer than a screen may stand comes back from the bar as no route at
 *  all, and the pane the member built is replaced by a line saying the link is not valid. The row
 *  the member has stands instead, and states why the press did nothing. */
test("a cmd-press past the cap stands nothing, and the address still names the screen", async () => {
  wireFull();
  const tracked = trackedWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECISIONS[0].summary }));
  await screen.findByRole("region", { name: DECISIONS[0].name });
  for (const row of DECISIONS.slice(1)) {
    fireEvent.click(bullet(row.summary), { metaKey: true });
  }
  await screen.findByRole("region", { name: DECISIONS.at(-1)!.name });
  expect(standing()).toEqual(DECISIONS.map((row) => row.name));

  fireEvent.click(bullet(PREFERENCES[0].summary), { metaKey: true });

  expect(standing()).toEqual(DECISIONS.map((row) => row.name));
  expect(screen.queryByRole("region", { name: PREFERENCES[0].name })).toBeNull();
  expect(tracked.place().opens).toEqual(
    DECISIONS.map((row) => "object/" + AGENT.id + "/memory/" + row.name),
  );
  expect(parseHash(tracked.address())).toEqual({
    kind: "section",
    section: "wiki",
    place: tracked.place(),
  });
  expect(
    screen.getByText("This screen holds " + TRACK_MAX_SLOTS + " slots. Close one to open another."),
  ).toBeTruthy();
});

/** The lane at the head is the list the whole path was opened from, so the row makes way from the
 *  other end: shutting any lane takes the line away and the next press stands again. */
test("closing a lane makes room the next press takes", async () => {
  wireFull();
  const tracked = trackedWiki();

  await userEvent.click(await screen.findByRole("button", { name: DECISIONS[0].summary }));
  await screen.findByRole("region", { name: DECISIONS[0].name });
  for (const row of DECISIONS.slice(1)) {
    fireEvent.click(bullet(row.summary), { metaKey: true });
  }
  await screen.findByRole("region", { name: DECISIONS.at(-1)!.name });

  await userEvent.click(screen.getByRole("button", { name: "Close " + DECISIONS.at(-1)!.name }));
  expect(screen.queryByText(/Close one to open another/)).toBeNull();

  fireEvent.click(bullet(PREFERENCES[0].summary), { metaKey: true });

  expect(await screen.findByRole("region", { name: PREFERENCES[0].name })).toBeTruthy();
  expect(standing().at(0)).toBe(DECISIONS[0].name);
  expect(standing().at(-1)).toBe(PREFERENCES[0].name);
  expect(standing()).toHaveLength(TRACK_MAX_SLOTS);
  expect(parseHash(tracked.address())).toEqual({
    kind: "section",
    section: "wiki",
    place: tracked.place(),
  });
});
