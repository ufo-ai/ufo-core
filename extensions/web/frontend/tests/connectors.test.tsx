import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { BRAND_MARKS } from "@/lib/brandMark";
import { PROVIDER_GLYPHS } from "@/lib/providerGlyph";
import { HOME_CONNECTORS_LANE, HOME_NEW_LANE, homeHash, parseHash, sectionHash } from "@/lib/route";

import {
  AGENT,
  AGENT_ID,
  MEMBER,
  SECOND,
  SECOND_ID,
  SETTINGS,
  StreamFake,
  TURN_ID,
  declaredFloor,
  fact,
  json,
  openAgentSettings,
  pageFits,
  pick,
  pressItem,
  pressRow,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  useStreamFake();
});

/** The catalog stripped to nothing, so a pool row is headed by the provider slug the connection
 *  carries and these tests state the pool alone. */
const BARE = { providers: [], connectors: [] };

/** The catalog as the read serves it: a tile per tool, the two the pages install themselves, and
 *  the group each stands under. */
const CATALOG = {
  providers: [
    {
      name: "slack",
      label: "Slack",
      summary: "Answer in channels and direct messages.",
      group: "Communication",
    },
    {
      name: "github",
      label: "GitHub",
      summary: "Read repositories, open issues, and push changes.",
      group: "Projects and code",
    },
    {
      name: "notion",
      label: "Notion",
      summary: "Search pages and update databases.",
      group: "Files and documents",
    },
    {
      name: "gmail",
      label: "Gmail",
      summary: "Search, read, and draft email.",
      group: "Email and calendar",
    },
  ],
  connectors: [
    { name: "slack", label: "Slack", installed: false },
    { name: "github", label: "GitHub", installed: true },
  ],
};

const COVERAGE = { api: true, sources: true };

/** The slots the address states, in the order the track stands them. */
function track(): string[] {
  const query = location.hash.split("?")[1] ?? "";
  const open = new URLSearchParams(query).get("open");
  return open ? open.split("~") : [];
}

/** One tool's row, found by the name it is headed with. Every act on the page says `Connect`, so a
 *  test reaches the act through the row rather than through the word. */
function row(label: string): HTMLElement {
  const item = screen.getByText(label).closest("li");
  if (!item) throw new Error("no row headed " + label);
  return item as HTMLElement;
}

function connects(label: string): HTMLElement {
  return within(row(label)).getByRole("button", { name: "Connect" });
}

/** One tool's row while a record of the same name stands beside the list: the crumb over that
 *  record carries the label too, and the lane stands after the list, so the row is the first
 *  item the label is found in. */
function item(label: string): HTMLElement {
  const found = screen
    .getAllByText(label)
    .map((node) => node.closest("li"))
    .find(Boolean);
  if (!found) throw new Error("no row headed " + label);
  return found;
}

/** The press that opens beside: the browser's own gesture for a second tab. One `userEvent`
 *  instance holds the key down over the click — the module's own verbs each set up a fresh one and
 *  would let go of it in between. */
async function besideItem(label: string): Promise<void> {
  const user = userEvent.setup();
  await user.keyboard("{Meta>}");
  await user.click(item(label));
  await user.keyboard("{/Meta}");
}

/** The library, read with the catalog the workspace offers. */
function library(routes: Record<string, (url: string, init?: RequestInit) => Response> = {}) {
  return wire({
    "/workspace/first-run": () => json(CATALOG),
    "/connections": () => json({ connections: [] }),
    "/github/coverage": () => json(COVERAGE),
    "/transcript": () => json({ messages: [] }),
    ...routes,
  });
}

/** The member coming back from the provider's pages: the tab they left is looked at again, which
 *  re-reads what the row states. */
async function returning() {
  for (const state of ["hidden", "visible"]) {
    Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
  }
}

function grant(provider: string, shared: boolean, name: string) {
  return {
    provider,
    account_id: "acct",
    account_label: null,
    owner_email: "member@example.com",
    own: true,
    shared,
    connected_at: "2026-07-01T00:00:00",
    grant: name,
    agents: [],
  };
}

function connectors() {
  return wire({
    "/connections": (url) =>
      json({
        connections: url.includes(SECOND_ID)
          ? [grant("notion", true, "g2")]
          : [grant("github", false, "g1")],
      }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
}

test("the connectors section lists the connection pool", async () => {
  location.hash = sectionHash("connectors");
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.getByText(/Only you/)).toBeTruthy();
});

/** The connectors screen stands as an app entry on home: the picker lists it under the workspace's
 *  apps, and the press stands the pool in a lane of its own, which the address carries. */
test("the picker stands the connectors screen in a lane", async () => {
  location.hash = homeHash({ opens: [AGENT_ID] });
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  location.hash = homeHash({ opens: [HOME_NEW_LANE, AGENT_ID] });
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  const picker = await screen.findByRole("region", { name: "New tab" });
  await userEvent.click(within(picker).getByRole("button", { name: /^Connectors/ }));

  expect(await screen.findByRole("region", { name: "Connectors" })).toBeTruthy();
  expect(track()).toEqual([HOME_CONNECTORS_LANE, AGENT_ID]);
  expect(await screen.findByText("github")).toBeTruthy();
});

/** The screen is one record, so it stands in one lane: a pick made while it stands closes the
 *  picker on it rather than opening a second host over it. */
test("a second pick of the connectors screen keeps the one lane", async () => {
  location.hash = homeHash({ opens: [HOME_CONNECTORS_LANE, AGENT_ID] });
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  location.hash = homeHash({ opens: [HOME_NEW_LANE, HOME_CONNECTORS_LANE, AGENT_ID] });
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  const picker = await screen.findByRole("region", { name: "New tab" });
  await userEvent.click(within(picker).getByRole("button", { name: /^Connectors/ }));

  await waitFor(() => expect(track()).toEqual([HOME_CONNECTORS_LANE, AGENT_ID]));
});

/** The screen stands on a workspace tab of its own, beside the other workspace-wide screens, so a
 *  member reaches the pool from the strip rather than from a link alone. */
test("the connectors tab stands in the workspace strip", async () => {
  location.hash = "#/workspace/team";
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("tab", { name: "Connectors" }));

  expect(parseHash(location.hash)).toEqual({ kind: "workspace", view: "connectors", place: {} });
  expect(await screen.findByText("github")).toBeTruthy();
});

test("the connectors workspace address stands the pool on its tab", async () => {
  location.hash = "#/workspace/connectors";
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Connectors" }).getAttribute("aria-selected")).toBe(
    "true",
  );
});

/** The two legs are GitHub's own, so they are read in GitHub's record and stand nowhere on the
 *  page's own ground. */
test("the GitHub row opens the coverage its install stands on", async () => {
  location.hash = sectionHash("connectors");
  library({ "/github/coverage": () => json({ api: true, sources: false }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("GitHub connected");
  expect(screen.queryByText("API")).toBeNull();

  await pressItem("GitHub");

  const sheet = await screen.findByRole("dialog", { name: "GitHub" });
  const header = sheet.querySelector("[data-slot=sheet-header]");
  expect(header?.firstElementChild).toBe(within(sheet).getByRole("button", { name: "Close" }));
  expect(fact("API")).toBe("Connected");
  expect(fact("Sources")).toBe("Not connected");
  expect([...sheet.querySelectorAll("dt")].map((leg) => leg.textContent)).toEqual([
    "API",
    "Sources",
  ]);
});

/** The library is the root of the path, so a second row leads there instead of piling up beside
 *  the first: what the member is reading is the record they last pressed. */
test("a second connector row replaces the first, and the address carries one", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("GitHub");
  expect(await screen.findByRole("dialog", { name: "GitHub" })).toBeTruthy();

  await pressItem("Notion");

  expect(await screen.findByRole("dialog", { name: "Notion team" })).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "GitHub" })).toBeNull());
  expect(track()).toEqual(["connection/g1"]);
});

/** Two accounts read side by side is a deliberate act, and it is the browser's own gesture for
 *  opening beside rather than in place. */
test("a modifier press keeps one connector sheet visible over the path", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("GitHub");
  expect(await screen.findByRole("dialog", { name: "GitHub" })).toBeTruthy();

  await besideItem("Notion");

  expect(await screen.findByRole("dialog", { name: "Notion team" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "GitHub" })).toBeNull();
  expect(track()).toEqual(["github-coverage", "connection/g1"]);
});

test("the middle button keeps one connector sheet visible over the path", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("GitHub");
  expect(await screen.findByRole("dialog", { name: "GitHub" })).toBeTruthy();

  await userEvent.pointer({ target: item("Notion"), keys: "[MouseMiddle]" });

  expect(await screen.findByRole("dialog", { name: "Notion team" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "GitHub" })).toBeNull();
  expect(track()).toEqual(["github-coverage", "connection/g1"]);
});

/** Pressing what already stands is not an act: the record is not built again, and Back still
 *  leaves the screen rather than walking through arrivals at it. */
test("pressing the row whose record already stands changes nothing", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("Notion");
  const record = await screen.findByRole("dialog", { name: "Notion team" });
  const steps = history.length;

  await pressItem("Notion");

  expect(screen.getByRole("dialog", { name: "Notion team" })).toBe(record);
  expect(track()).toEqual(["connection/g1"]);
  expect(history.length).toBe(steps);
});

/** Finder marks the row every open column was reached through. Without it a truncated track reads
 *  as slots vanishing rather than as one path. */
test("the row the standing record was opened from is marked", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("Notion");
  await screen.findByRole("dialog", { name: "Notion team" });

  expect(item("Notion").getAttribute("aria-current")).toBe("true");
  expect(item("Notion").className).toContain("bg-fill");
  expect(item("GitHub").getAttribute("aria-current")).toBeNull();
});

test("a link carrying a path shows only its last record", async () => {
  location.hash = sectionHash("connectors", { opens: ["github-coverage", "connection/g1"] });
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("dialog", { name: "Notion team" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "GitHub" })).toBeNull();
});

/** A record opened from another means nothing without it, so shutting a lane shuts what stands
 *  after it. Shutting the last one leaves everything before it standing. */
test("closing through a path exposes one sheet at a time", async () => {
  location.hash = sectionHash("connectors", { opens: ["github-coverage", "connection/g1"] });
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const notion = await screen.findByRole("dialog", { name: "Notion team" });
  await userEvent.click(within(notion).getByRole("button", { name: "Close" }));

  const github = await screen.findByRole("dialog", { name: "GitHub" });
  expect(screen.queryByRole("dialog", { name: "Notion team" })).toBeNull();
  await userEvent.click(within(github).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "GitHub" })).toBeNull());
  expect(track()).toEqual([]);
});

test("closing the last record leaves the one it was opened from standing", async () => {
  location.hash = sectionHash("connectors", { opens: ["github-coverage", "connection/g1"] });
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const notion = await screen.findByRole("dialog", { name: "Notion team" });
  await userEvent.click(within(notion).getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Notion team" })).toBeNull());
  expect(screen.getByRole("dialog", { name: "GitHub" })).toBeTruthy();
  expect(track()).toEqual(["github-coverage"]);
});

test("the pool narrows on the toolbar's search, which names what it searches", async () => {
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () =>
      json({ connections: [grant("github", false, "g1"), grant("notion", true, "g2")] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("notion")).toBeTruthy();
  const box = screen.getByLabelText("Search connectors");
  expect(document.querySelector('[data-slot="header"]')!.contains(box)).toBe(false);

  await userEvent.type(box, "github{enter}");

  await waitFor(() => expect(screen.queryByText("notion")).toBeNull());
  expect(screen.getByText("github")).toBeTruthy();
  expect(location.hash).toContain("q=github");
});

test("the agent's own edges fit that same desktop", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  const across = declaredFloor((await screen.findByText("github")).closest("table")!);
  expect(pageFits(across)).toBe(true);
  expect(across).toBe(
    "calc(2 * var(--size-fact-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

test("the agent's settings read its attached connections", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  const { calls } = connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  expect(await screen.findByText("github")).toBeTruthy();
  expect(calls.some((url) => url.includes("/agents/" + AGENT_ID + "/connections"))).toBe(true);
});

test("the pool's record states what the row gave up, and attaches to the agent named on it", async () => {
  const posted: string[] = [];
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Attached." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("github");

  expect(fact("Owner")).toBe("You");
  expect(fact("Connected")).toBe("Jul 1 2026");

  await pick("App", "Second");
  await userEvent.click(screen.getByRole("button", { name: "Attach to app" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("the pool's record states every stream and the errors for its account once sources load", async () => {
  location.hash = sectionHash("connectors");
  let answer: (response: Response) => void = () => {};
  const names = [
    "commits",
    "deployments",
    "discussions",
    "issues",
    "pulls",
    "releases",
    "workflows",
  ];
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/workspace/sources": () =>
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("github");

  expect(await screen.findByText("You")).toBeTruthy();
  expect(screen.queryByText("Streams")).toBeNull();
  expect(screen.queryByText("Errors")).toBeNull();

  await act(async () =>
    answer(
      json({
        sources: names
          .map((stream) => ({
            backend: "github",
            account_id: "acct",
            stream,
            consecutive_errors: stream === "issues" ? 2 : 0,
            parked_reason: stream === "workflows" ? "Reconnect GitHub." : null,
          }))
          .concat({
            backend: "slack",
            account_id: "other",
            stream: "messages",
            consecutive_errors: 9,
            parked_reason: null,
          }),
      }),
    ),
  );
  const said = names.join(", ");
  const value = await screen.findByText(said);
  expect(fact("Streams")).toBe(said);
  expect(fact("Errors")).toBe("2 · 1 parked");
  expect(screen.getByTitle("Reconnect GitHub.")).toBeTruthy();
  expect(value.className).toContain("whitespace-pre-wrap");
  expect(value.className).not.toContain("truncate");
});

test("the pool's record shares and revokes into the lane of the agent already holding the grant", async () => {
  const posted: string[] = [];
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () =>
      json({
        connections: [{ ...grant("github", true, "g1"), agents: [{ id: SECOND_ID, name: "second" }] }],
      }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("github");
  await userEvent.click(screen.getByRole("button", { name: "Make private" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + SECOND_ID + "/intents");

  await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));

  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1]).toContain("/agents/" + SECOND_ID + "/intents");
});

test("a grant change is admitted into the lane of the agent whose settings hold it", async () => {
  const posted: string[] = [];
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/settings": () => json(SETTINGS),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Shared." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  await pressRow("github");

  expect(fact("Owner")).toBe("You");
  expect(fact("Connected")).toBe("Jul 1 2026");

  await userEvent.click(screen.getByRole("button", { name: "Share with app" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + AGENT_ID + "/intents");
});

test("the agent's record revokes the grant it stands on", async () => {
  const posted: string[] = [];
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": () => json({ connections: [grant("github", true, "g1")] }),
    "/settings": () => json(SETTINGS),
    "/intents": (url) => {
      posted.push(url);
      return json({ applied: true, message: "Revoked." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  await pressRow("github");
  await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toContain("/agents/" + AGENT_ID + "/intents");
});

test("a revoked connection stays shut when the grant comes back on a later read", async () => {
  let served = 0;
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": (url) => {
      if (!url.includes("/agents/"))
        return json({ connections: [{ ...grant("github", true, "g1"), agents: [] }] });
      served += 1;
      return json({ connections: served === 2 ? [] : [grant("github", true, "g1")] });
    },
    "/intents": () => json({ applied: true, message: "Applied." }),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  await pressRow("github");
  expect(await screen.findByRole("dialog", { name: "acct" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "acct" })).toBeNull());

  await userEvent.click(await screen.findByRole("combobox", { name: "Connection" }));
  await userEvent.click(await screen.findByRole("option", { name: /github/ }));
  await userEvent.click(screen.getByRole("button", { name: "Attach" }));

  expect(await screen.findByRole("cell", { name: "github" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "acct" })).toBeNull();
});

test("the attach picker names the provider and the account, not the broker id", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": (url) =>
      json({
        connections: url.includes("/agents/")
          ? []
          : [{ ...grant("github", true, "g1"), account_label: "Work GitHub", agents: [] }],
      }),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  await userEvent.click(await screen.findByRole("combobox", { name: "Connection" }));

  const option = await screen.findByRole("option", { name: /Work GitHub/ });
  expect(option.textContent).toContain("github");
  expect(option.textContent).toContain("You");
  expect(option.textContent).not.toContain("acct");
});

test("the account column names the account the member holds, not the broker id", async () => {
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const row = (await screen.findAllByText("github")).map((node) => node.closest("li")).find(Boolean)!;
  expect(within(row).getByText(/member@example\.com/)).toBeTruthy();
  expect(within(row).queryByText(/acct/)).toBeNull();
});

test("an empty pool stands the catalog alone, with no connected zone", async () => {
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () => json({ connections: [] }),
    "/github/coverage": () => json({ api: false, sources: false }),
    "/workspace/first-run": () => json({ ...CATALOG, connectors: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("heading", { name: "Available" });
  expect(screen.queryByRole("heading", { name: "Connected" })).toBeNull();
});

test("the bar is drawn while the first read is still in flight", async () => {
  const pending = new Map<string, (value: Response) => void>();
  location.hash = "#/agents/" + AGENT_ID;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/connections")) {
        return new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        });
      }
      if (url.includes("/settings")) return json(SETTINGS);
      if (url.includes("/api/agents/status")) return json({ statuses: [] });
      if (url.includes("/objects/conversation")) return json({ objects: [] });
      return json({ chats: [] });
    }),
  );
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  expect(await screen.findByRole("combobox", { name: "Connection" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add connector" })).toBeTruthy();
  expect(pending.size).toBe(2);
});

test("the agent's own section states what is shared with that agent", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": () => json({ connections: [grant("github", true, "g1")] }),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings("Assistant", "Connectors");

  expect(await screen.findByText("github")).toBeTruthy();
});

/** The agent's own dialog holds three reads of it, and the connectors are one of them — reached by
 *  their own tab rather than scrolled past under the spec form. */
test("the agent's connectors stand as their own read of its settings panel", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const dialog = within(await openAgentSettings("Assistant", "Connectors"));
  expect(await dialog.findByRole("cell", { name: "github" })).toBeTruthy();
  expect(dialog.getByRole("button", { name: "Add connector" })).toBeTruthy();

  // The band names the read showing, and the other two stand under it.
  await userEvent.click(dialog.getByRole("button", { name: "Connectors" }));
  expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
    "Settings",
    "Connectors",
    "Scheduled",
  ]);

  await userEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
  expect(await dialog.findByRole("button", { name: "Edit prompt" })).toBeTruthy();
  expect(dialog.queryByRole("button", { name: "Add connector" })).toBeNull();
});

test("the settings dialog replaces a grant sheet with the add sheet", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = await openAgentSettings("Assistant", "Connectors");

  await pressRow("github");
  expect(await screen.findByRole("dialog", { name: "acct" })).toBeTruthy();

  await userEvent.click(within(dialog).getByRole("button", { name: "Add connector" }));

  expect(await screen.findByRole("dialog", { name: "Add connector" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "acct" })).toBeNull();
});

test("a connector's consent opens in a window this page owns, so its return page closes itself", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": () => json({ connections: [] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
    "/intents": () => json({ applied: true, message: "", turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  const dialog = await openAgentSettings("Assistant", "Connectors");

  await userEvent.click(within(dialog).getByRole("button", { name: "Add connector" }));
  await userEvent.type(await screen.findByLabelText("Provider"), "notion");
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));

  // The window opened on the press, before the verb had minted anything: opening one after the
  // round trip would have lost the gesture the browser opens it for.
  const [blank, name, features] = opened.mock.calls[0];
  expect(blank).toBe("");
  expect(name).toBe("ufo-connect");
  expect(features).toContain("popup");
  expect(consent.focus).toHaveBeenCalled();

  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

  // The frame carrying the link lands in that window, so the panel puts nothing under the act.
  await waitFor(() => expect(consent.location.href).toBe("/surface/web/turns/" + TURN_ID + "/connect"));
  expect(screen.queryByRole("link", { name: "Open the provider consent page" })).toBeNull();
  opened.mockRestore();
});

test("a connector consent the browser refuses still renders the link", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  wire({
    "/connections": () => json({ connections: [] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/settings": () => json(SETTINGS),
    "/transcript": () => json({ messages: [] }),
    "/intents": () => json({ applied: true, message: "", turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  const dialog = await openAgentSettings("Assistant", "Connectors");

  await userEvent.click(within(dialog).getByRole("button", { name: "Add connector" }));
  await userEvent.type(await screen.findByLabelText("Provider"), "notion");
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

  const link = await screen.findByRole("link", { name: "Open the provider consent page" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

const SLACK_LINK = "https://slack.com/oauth/v2/authorize?state=sealed";

const POOLED_NOTION = {
  connections: [
    {
      provider: "notion",
      account_id: "acct-1",
      account_label: "Notion team",
      owner_email: "member@example.com",
      own: true,
      shared: false,
      connected_at: "2026-08-01T09:00:00",
      grant: "g1",
      agents: [],
    },
  ],
};

/** Two accounts already connected, so a removal has to be one row's act: the account the member
 *  named goes and the account beside it stays. */
const POOLED_PAIR = {
  connections: [POOLED_NOTION.connections[0], grant("gmail", true, "g2")],
};

/** The row's own remove, named for the tool it stands on so two rows never offer one word. */
function removes(label: string): HTMLElement {
  return within(row(label)).getByRole("button", { name: "Remove " + label });
}

test("a connected row removes that one account behind a confirmation naming it", async () => {
  const posted: { url: string; body: unknown }[] = [];
  let pool: unknown = POOLED_PAIR;
  location.hash = sectionHash("connectors");
  library({
    "/connections": () => json(pool),
    "/intents": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      pool = { connections: [POOLED_PAIR.connections[1]] };
      return json({ applied: true, message: "Saved." });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  await userEvent.click(removes("Notion"));

  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("heading", { name: "Remove Notion" })).toBeTruthy();
  expect(within(dialog).getByText(/Notion account Notion team/)).toBeTruthy();

  await userEvent.click(within(dialog).getByRole("button", { name: "Remove account" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].url).toContain("/agents/" + AGENT_ID + "/intents");
  expect(posted[0].body).toEqual({ verb: "delete", kind: "connection", name: "g1" });

  // The list re-reads itself, so the row goes without the member reloading the page.
  await waitFor(() => expect(screen.queryByLabelText("Notion connected")).toBeNull());
  expect(screen.getByLabelText("Gmail connected")).toBeTruthy();
});

test("the confirmation cancels and nothing is disconnected", async () => {
  const posted: unknown[] = [];
  location.hash = sectionHash("connectors");
  library({
    "/connections": () => json(POOLED_PAIR),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  await userEvent.click(removes("Notion"));
  const dialog = await screen.findByRole("dialog");
  await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(posted.length).toBe(0);
  expect(screen.getByLabelText("Notion connected")).toBeTruthy();
});

/** The gate is the kind's, not the button's: a member the disconnect refuses reads that refusal
 *  where they pressed, and the account is still there behind it. */
test("a refused removal states itself in the confirmation and the account stays", async () => {
  const refusal = "only the connection owner or a workspace admin may disconnect an account";
  location.hash = sectionHash("connectors");
  library({
    "/connections": () => json(POOLED_PAIR),
    "/intents": () => json({ applied: false, message: refusal }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  await userEvent.click(removes("Notion"));
  await userEvent.click(
    within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove account" }),
  );

  expect(await screen.findByText(refusal)).toBeTruthy();
  expect(screen.getByLabelText("Notion connected")).toBeTruthy();
});

/** Where the act would be refused it is not drawn: another member's shared account, and a workspace
 *  install, which carries no connection to disconnect. */
test("a row the member holds no claim on draws no remove", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () =>
      json({
        connections: [
          {
            ...POOLED_NOTION.connections[0],
            own: false,
            shared: true,
            owner_email: "other@example.com",
          },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  expect(within(row("Notion")).queryByRole("button", { name: "Remove Notion" })).toBeNull();
  expect(within(row("GitHub")).queryByRole("button", { name: "Remove GitHub" })).toBeNull();
});

test("the library offers every catalog tool and hoists the connected ones", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Connected" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Available" })).toBeTruthy();
  expect(connects("Slack")).toBeTruthy();
  expect(connects("Gmail")).toBeTruthy();
  expect(screen.getByLabelText("GitHub connected")).toBeTruthy();
  expect(screen.getByLabelText("Notion connected")).toBeTruthy();
  expect(within(row("Notion")).queryByRole("button", { name: "Connect" })).toBeNull();
});

test("the library adds providers from the broker catalog", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connector-catalog": () =>
      json({ providers: [{ name: "salesforce", label: "Salesforce" }], after: null }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: /More connectors/ })).toBeTruthy();
  expect(connects("Salesforce")).toBeTruthy();
  expect(within(row("Salesforce")).getByText("Connect this account to use its tools.")).toBeTruthy();
});

test("a broker catalog failure leaves held and fixed connectors available", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () => json(POOLED_NOTION),
    "/connector-catalog": () => new Response(null, { status: 503 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByLabelText("Notion connected")).toBeTruthy();
  expect(screen.getByLabelText("GitHub connected")).toBeTruthy();
  expect(connects("Slack")).toBeTruthy();
  expect(screen.getByText("Error 503 — reload to retry.")).toBeTruthy();
});

test("the library follows broker cursors until one press adds 25 to 50 connectors", async () => {
  const calls: string[] = [];
  location.hash = sectionHash("connectors");
  library({
    "/connector-catalog": (url) => {
      calls.push(url);
      const after = new URL(url, "https://ufo.test").searchParams.get("after");
      if (!after) {
        return json({
          providers: [{ name: "salesforce", label: "Salesforce" }],
          after: "page-2",
        });
      }
      const page = Number(after.replace("page-", ""));
      if (!Number.isInteger(page) || page < 2 || page > 6) {
        throw new Error("unexpected catalog cursor " + after);
      }
      return json({
        providers: Array.from({ length: 6 }, (_, index) => ({
          name: `connector-${page}-${index + 1}`,
          label: `Connector ${page}-${index + 1}`,
        })),
        after: "page-" + (page + 1),
      });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("Salesforce")).toBeTruthy();
  expect(connects("Salesforce")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Load more" }));

  expect(await screen.findByText("Connector 6-6")).toBeTruthy();
  expect(screen.getByText("Connector 2-1")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Load more" })).toBeTruthy();
  expect(calls.filter((url) => url.includes("after=")).length).toBe(5);
  expect(calls.at(-1)).toContain("after=page-6");
});

test("a connected row names the account it stands on and who reaches it", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () =>
      json({
        connections: [
          {
            ...POOLED_NOTION.connections[0],
            agents: [
              { id: AGENT_ID, name: "assistant" },
              { id: SECOND_ID, name: "second" },
            ],
          },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  expect(
    within(row("Notion")).getByText(/Apps: Assistant, Second · Notion team · Only you/),
  ).toBeTruthy();
  // A workspace install carries no connection row, so its row keeps the catalog's own sentence.
  expect(within(row("GitHub")).getByText(CATALOG.providers[1].summary)).toBeTruthy();
});

test("a connected row with no app states that no app holds its grant", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  expect(within(row("Notion")).getByText(/No apps · Notion team · Only you/)).toBeTruthy();
});

test("provider marks keep their own shape", async () => {
  location.hash = sectionHash("connectors");
  library();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("heading", { name: "Available" });
  const mark = row("Slack").querySelector<HTMLElement>('[style*="--brand-slack"]');
  expect(mark).not.toBeNull();
  expect(mark!.className).not.toContain("rounded-full");
});

/** The brokers reach further than the catalog names, so the pool is listed whole: a connection on a
 *  provider no tile offers still stands, headed by the slug it carries. */
test("a connection outside the catalog still stands in the library", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () => json({ connections: [grant("sentry", false, "g9")] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByLabelText("sentry connected")).toBeTruthy();
});

test("an available row stands under the group the catalog gives it", async () => {
  location.hash = sectionHash("connectors");
  library();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: /Communication/ })).toBeTruthy();
  expect(screen.getByRole("heading", { name: /Email and calendar/ })).toBeTruthy();
  expect(within(row("Slack")).getByText(CATALOG.providers[0].summary)).toBeTruthy();
});

/** Everything the catalog offers is connected, so the page is the connected zone alone. */
test("a fully connected catalog stands as one zone", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/workspace/first-run": () =>
      json({ providers: [CATALOG.providers[2]], connectors: [] }),
    "/connections": () => json(POOLED_NOTION),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Connected" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Available" })).toBeNull();
});

test("the search narrows the catalog and states when nothing matches", async () => {
  location.hash = sectionHash("connectors");
  library();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByRole("heading", { name: "Available" });

  await userEvent.type(screen.getByLabelText("Search connectors"), "notion{Enter}");
  expect(connects("Notion")).toBeTruthy();
  expect(screen.queryByText("Slack")).toBeNull();

  await userEvent.clear(screen.getByLabelText("Search connectors"));
  await userEvent.type(screen.getByLabelText("Search connectors"), "salesforce{Enter}");
  expect(await screen.findByText("No connector matches this search.")).toBeTruthy();
});

test("the search reads providers outside the fixed catalog", async () => {
  const calls: string[] = [];
  location.hash = sectionHash("connectors");
  library({
    "/connector-catalog": (url) => {
      calls.push(url);
      return json({
        providers: url.includes("q=salesforce")
          ? [{ name: "salesforce", label: "Salesforce" }]
          : [],
        after: null,
      });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByRole("heading", { name: "Available" });

  await userEvent.type(screen.getByLabelText("Search connectors"), "salesforce{Enter}");

  expect(await screen.findByText("Salesforce")).toBeTruthy();
  expect(calls.some((url) => url.includes("q=salesforce"))).toBe(true);
});

test("a library row connects the member's account through the main agent", async () => {
  const posted: { url: string; body: unknown }[] = [];
  location.hash = sectionHash("connectors");
  library({
    "/intents": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "", turn_id: TURN_ID });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const consent = { focus: vi.fn(), close: vi.fn(), location: { href: "" } };
  const opened = vi.spyOn(window, "open").mockReturnValue(consent as unknown as Window);

  await screen.findByRole("heading", { name: "Available" });
  await userEvent.click(connects("Notion"));

  expect(posted[0].url).toContain("/agents/" + AGENT_ID + "/intents");
  expect(posted[0].body).toEqual({
    verb: "connect",
    kind: "connection",
    name: "notion",
    spec: { shared: false },
  });
  expect(opened.mock.calls[0][1]).toBe("ufo-connect");

  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

  await waitFor(() =>
    expect(consent.location.href).toBe("/surface/web/turns/" + TURN_ID + "/connect"),
  );
  expect(screen.queryByRole("link", { name: "Open the provider consent page" })).toBeNull();
  opened.mockRestore();
});

test("a library consent the browser refuses still renders the link", async () => {
  location.hash = sectionHash("connectors");
  library({ "/intents": () => json({ applied: true, message: "", turn_id: TURN_ID }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("heading", { name: "Available" });
  await userEvent.click(connects("Notion"));
  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

  const link = await screen.findByRole("link", { name: "Open the provider consent page" });
  expect(link.getAttribute("href")).toBe("/surface/web/turns/" + TURN_ID + "/connect");
});

test("a workspace install posts the surface object's own action and hands back its install page", async () => {
  const posted: unknown[] = [];
  location.hash = sectionHash("connectors");
  library({
    "/slack_connect": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "", url: SLACK_LINK });
    },
    "/actions/surface/slack": () =>
      json({
        actions: [
          {
            name: "slack_connect",
            description: "Install ufo in Slack.",
            input_schema: { properties: {} },
            call: { kind: "surface", action: "slack_connect", name: "slack", input: {} },
            label: "Connect",
          },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("heading", { name: "Available" });
  await userEvent.click(connects("Slack"));

  await waitFor(() =>
    expect(posted[0]).toEqual({
      url: "/surface/web/agents/" + AGENT.id + "/actions/surface/slack/slack_connect",
      body: {},
    }),
  );
  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
});

test("a refused intent states itself and the row stays open", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/slack_connect": () =>
      json({ applied: false, message: "Only a workspace admin connects Slack." }),
    "/actions/surface/slack": () =>
      json({
        actions: [
          {
            name: "slack_connect",
            description: "Install ufo in Slack.",
            input_schema: { properties: {} },
            call: { kind: "surface", action: "slack_connect", name: "slack", input: {} },
            label: "Connect",
          },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("heading", { name: "Available" });
  await userEvent.click(connects("Slack"));

  expect(await screen.findByText("Only a workspace admin connects Slack.")).toBeTruthy();
  expect(connects("Slack")).toBeTruthy();
});

test("the row moves up when the account lands", async () => {
  let pool: unknown = { connections: [] };
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(pool) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByRole("heading", { name: "Available" });
  expect(connects("Notion")).toBeTruthy();

  pool = POOLED_NOTION;
  await returning();

  expect(await screen.findByLabelText("Notion connected")).toBeTruthy();
  expect(within(row("Notion")).queryByRole("button", { name: "Connect" })).toBeNull();
});

test("the category picker narrows both zones and lands in the place", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByLabelText("Notion connected");

  await pick("Category", "Communication");

  expect(location.hash).toContain("chip=Communication");
  expect(connects("Slack")).toBeTruthy();
  // Notion is connected but stands under another category, so its zone goes with it.
  expect(screen.queryByText("Notion")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Connected" })).toBeNull();
  expect(screen.queryByText("Gmail")).toBeNull();
});

/** A connection on a provider the catalog does not name has no category, so a picked one hides it
 *  rather than sweeping it into a group it never stood under. */
test("a connection outside the catalog stands only under every category", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json({ connections: [grant("sentry", false, "g9")] }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByLabelText("sentry connected");

  await pick("Category", "Communication");
  expect(screen.queryByLabelText("sentry connected")).toBeNull();
});

/** A connector no mark names draws the plain plug, so every biz-ops row beside a vendored provider
 *  would read as the same unnamed connector. */
test("each biz-ops connector is drawn by a mark of its own", () => {
  for (const slug of ["apollo", "brex", "docusign", "mercury", "pandadoc", "ramp", "xero"]) {
    expect(BRAND_MARKS.has(slug) || slug in PROVIDER_GLYPHS).toBe(true);
  }
});
