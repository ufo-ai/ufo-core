import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { BRAND_MARKS } from "@/lib/brandMark";
import { PROVIDER_GLYPHS } from "@/lib/providerGlyph";
import { sectionHash } from "@/lib/route";

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

const BARE = { providers: [], connectors: [] };

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

function track(): string[] {
  const query = location.hash.split("?")[1] ?? "";
  const open = new URLSearchParams(query).get("open");
  return open ? open.split("~") : [];
}

function row(label: string): HTMLElement {
  const item = screen.getByText(label).closest("li");
  if (!item) throw new Error("no row headed " + label);
  return item as HTMLElement;
}

function connects(label: string): HTMLElement {
  return within(row(label)).getByRole("button", { name: "Connect" });
}

function zone(title: string): HTMLElement {
  const found = screen.getByRole("heading", { name: title }).closest("section");
  if (!found) throw new Error("no zone headed " + title);
  return found as HTMLElement;
}

function item(label: string): HTMLElement {
  const found = screen
    .getAllByText(label)
    .map((node) => node.closest("li"))
    .find(Boolean);
  if (!found) throw new Error("no row headed " + label);
  return found;
}

async function besideItem(label: string): Promise<void> {
  const user = userEvent.setup();
  await user.keyboard("{Meta>}");
  await user.click(item(label));
  await user.keyboard("{/Meta}");
}

function library(routes: Record<string, (url: string, init?: RequestInit) => Response> = {}) {
  return wire({
    "/workspace/first-run": () => json(CATALOG),
    "/connections": () => json({ connections: [] }),
    "/github/coverage": () => json(COVERAGE),
    "/transcript": () => json({ messages: [] }),
    ...routes,
  });
}

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
    id: "c-" + name,
    provider,
    account_id: "acct",
    account_label: null,
    owner_email: "member@example.com",
    own: true,
    shared,
    base_url: null,
    backfill_days: null,
    connected_at: "2026-07-01T00:00:00",
    grant: name,
    agents: [],
  };
}

function stream(
  connection: string,
  backend: string,
  named: string,
  errors = 0,
  parked: string | null = null,
) {
  return {
    id: backend + "-" + named,
    connection_id: connection,
    backend,
    stream: named,
    consecutive_errors: errors,
    next_sync_at: "2026-07-01T00:01:00",
    parked_reason: parked,
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

const SLACK_INSTALL_ACTIONS = {
  actions: [
    {
      name: "slack_connect",
      description: "",
      input_schema: {},
      call: { kind: "surface", name: "slack", action: "slack_connect", input: {} },
      label: "Connect Slack",
    },
  ],
};

test("the connectors section lists the connection pool", async () => {
  location.hash = sectionHash("connectors");
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.getByText(/Only you/)).toBeTruthy();
});

test("a forwarded connect arrival is stated, and leaves the address", async () => {
  history.replaceState(
    null,
    "",
    location.pathname + "?connected=GitHub+%C2%B7+octo" + sectionHash("connectors"),
  );
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("GitHub · octo connected.")).toBeTruthy();
  expect(location.search).toBe("");
  expect(location.hash).toBe(sectionHash("connectors"));
});

test("the connectors screen states nothing where no arrival named an account", async () => {
  location.hash = sectionHash("connectors");
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByText(/connected\./)).toBeNull();
});

test("coming back to the connectors screen does not state the arrival again", async () => {
  history.replaceState(
    null,
    "",
    location.pathname + "?connected=GitHub+%C2%B7+octo" + sectionHash("connectors"),
  );
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  expect(await screen.findByText("GitHub · octo connected.")).toBeTruthy();

  location.hash = "#/workspace/team";
  window.dispatchEvent(new HashChangeEvent("hashchange"));
  await waitFor(() => expect(screen.queryByText("GitHub · octo connected.")).toBeNull());
  location.hash = sectionHash("connectors");
  window.dispatchEvent(new HashChangeEvent("hashchange"));

  expect(await screen.findByText("github")).toBeTruthy();
  expect(screen.queryByText("GitHub · octo connected.")).toBeNull();
});

test("connectors stays out of the workspace strip", async () => {
  location.hash = "#/workspace/team";
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("tab", { name: "Team" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Connectors" })).toBeNull();
});

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
  const box = screen.getByLabelText("Search connections");
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

test("the pool's record hangs its connection's streams under it, and no other connection's", async () => {
  location.hash = sectionHash("connectors");
  let answer: (response: Response) => void = () => {};
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
  expect(screen.queryByText("issues")).toBeNull();

  await act(async () =>
    answer(
      json({
        sources: [
          stream("c-g1", "github", "issues", 2),
          stream("c-g1", "github", "workflows", 0, "Reconnect GitHub."),
          stream("c-g2", "slack", "messages", 9),
        ],
      }),
    ),
  );

  expect(await screen.findByText("issues")).toBeTruthy();
  expect(screen.getByText("2 errors")).toBeTruthy();
  expect(screen.getByText("Reconnect GitHub.")).toBeTruthy();
  expect(screen.queryByText("messages")).toBeNull();
  expect(screen.queryByText("9 errors")).toBeNull();
});

test("a connection with no stream says so rather than standing blank", async () => {
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () => json({ connections: [grant("github", false, "g1")] }),
    "/workspace/sources": () => json({ sources: [stream("c-other", "slack", "messages")] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("github");

  expect(await screen.findByText("No stream syncs this account yet.")).toBeTruthy();
});

test("the record's sync settings land on the connection kind, and read back to a member who cannot set them", async () => {
  const posted: { url: string; body: string }[] = [];
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () =>
      json({
        connections: [
          { ...grant("github", false, "g1"), base_url: "https://acme.example.com", backfill_days: 30 },
        ],
      }),
    "/workspace/sources": () => json({ sources: [] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/intents": (url, init) => {
      posted.push({ url, body: String(init?.body ?? "") });
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("github");

  const url = await screen.findByLabelText("Tenant URL");
  expect((url as HTMLInputElement).value).toBe("https://acme.example.com");
  expect((screen.getByLabelText("Backfill days") as HTMLInputElement).value).toBe("30");

  await userEvent.clear(screen.getByLabelText("Backfill days"));
  await userEvent.type(screen.getByLabelText("Backfill days"), "90");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].url).toContain("/agents/" + AGENT_ID + "/intents");
  expect(JSON.parse(posted[0].body)).toMatchObject({
    verb: "apply",
    kind: "connection",
    name: "g1",
    spec: {
      provider: "github",
      account_id: "acct",
      base_url: "https://acme.example.com",
      backfill_days: 90,
    },
  });
});

test("a workspace connection states its access and draws neither owner nor make-private", async () => {
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () =>
      json({
        connections: [
          {
            ...grant("folder", true, "g1"),
            account_id: "",
            owner_email: null,
            own: true,
            agents: [{ id: SECOND_ID, name: "second" }],
          },
        ],
      }),
    "/workspace/sources": () => json({ sources: [] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("folder");

  expect(await screen.findByRole("dialog", { name: "folder" })).toBeTruthy();
  expect(fact("Access")).toBe("Workspace");
  expect(fact("Owner")).toBe("Workspace");
  expect(screen.queryByRole("button", { name: "Make private" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Share with app" })).toBeNull();
});

test("the record shares on the connection and revokes on the holder's own edge", async () => {
  const posted: { url: string; body: string }[] = [];
  location.hash = sectionHash("connectors");
  wire({
    "/connections": () =>
      json({
        connections: [{ ...grant("github", true, "g1"), agents: [{ id: SECOND_ID, name: "second" }] }],
      }),
    "/workspace/sources": () => json({ sources: [] }),
    "/github/coverage": () => json({ api: true, sources: true }),
    "/workspace/first-run": () => json(BARE),
    "/intents": (url, init) => {
      posted.push({ url, body: String(init?.body ?? "") });
      return json({ applied: true, message: "Applied." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  await pressItem("github");
  await userEvent.click(screen.getByRole("button", { name: "Make private" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].url).toContain("/agents/" + AGENT_ID + "/intents");
  expect(JSON.parse(posted[0].body)).toMatchObject({
    verb: "apply",
    kind: "connection",
    name: "g1",
    spec: {
      provider: "github",
      account_id: "acct",
      shared: false,
      base_url: "",
      backfill_days: null,
    },
  });

  await userEvent.click(await screen.findByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));

  await waitFor(() => expect(posted.length).toBe(2));
  expect(posted[1].url).toContain("/agents/" + SECOND_ID + "/intents");
  expect(JSON.parse(posted[1].body)).toMatchObject({ verb: "detach", kind: "connector_grant" });
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
  expect(await screen.findByRole("dialog", { name: "member@example.com" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm revoke" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "member@example.com" })).toBeNull());

  await userEvent.click(await screen.findByRole("combobox", { name: "Connection" }));
  await userEvent.click(await screen.findByRole("option", { name: /github/ }));
  await userEvent.click(screen.getByRole("button", { name: "Attach" }));

  expect(await screen.findByRole("cell", { name: "github" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "member@example.com" })).toBeNull();
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
  expect(screen.queryByRole("heading", { name: "Your connections" })).toBeNull();
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
      if (url.includes("/workspace/surfaces")) return json({ surfaces: [] });
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

test("the agent's connectors stand as their own read of its settings panel", async () => {
  location.hash = "#/agents/" + AGENT_ID;
  connectors();
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  const dialog = within(await openAgentSettings("Assistant", "Connectors"));
  expect(await dialog.findByRole("cell", { name: "github" })).toBeTruthy();
  expect(dialog.getByRole("button", { name: "Add connector" })).toBeTruthy();

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
  expect(await screen.findByRole("dialog", { name: "member@example.com" })).toBeTruthy();

  await userEvent.click(within(dialog).getByRole("button", { name: "Add connector" }));

  expect(await screen.findByRole("dialog", { name: "Add connector" })).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "member@example.com" })).toBeNull();
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

  const [blank, name, features] = opened.mock.calls[0];
  expect(blank).toBe("");
  expect(name).toBe("ufo-connect");
  expect(features).toContain("popup");
  expect(consent.focus).toHaveBeenCalled();
  expect(sessionStorage.getItem("ufo-consent-window")).toBe("1");

  StreamFake.last().emit("connect", { provider: "notion", label: "Notion", turn: TURN_ID });

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

const POOLED_PAIR = {
  connections: [POOLED_NOTION.connections[0], grant("gmail", true, "g2")],
};

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
  const shared = within(zone("Shared with the workspace"));
  expect(shared.queryByRole("button", { name: "Remove Notion" })).toBeNull();
  expect(shared.queryByRole("button", { name: "Remove GitHub" })).toBeNull();
});

test("a shared account stands under a heading of its own", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () =>
      json({
        connections: [
          POOLED_NOTION.connections[0],
          { ...grant("gmail", true, "g2"), own: false, owner_email: "other@example.com" },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  const own = within(zone("Your connections"));
  expect(own.getByLabelText("Notion connected")).toBeTruthy();
  expect(own.queryByLabelText("Gmail connected")).toBeNull();
  const shared = within(zone("Shared with the workspace"));
  expect(shared.getByLabelText("Gmail connected")).toBeTruthy();
  expect(shared.queryByRole("button", { name: "Remove Gmail" })).toBeNull();
});

test("an admin reads a colleague's account as the workspace's, not their own", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () =>
      json({
        connections: [
          { ...POOLED_NOTION.connections[0], shared: true, owner_email: "other@example.com" },
          grant("gmail", false, "g2"),
        ],
      }),
  });
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  const own = within(zone("Your connections"));
  expect(own.getByLabelText("Gmail connected")).toBeTruthy();
  expect(own.queryByLabelText("Notion connected")).toBeNull();
  expect(within(zone("Shared with the workspace")).getByLabelText("Notion connected")).toBeTruthy();
});

test("a provider another member shares still offers the member their own connect", async () => {
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
  expect(within(zone("Available")).getByText("Notion")).toBeTruthy();
});

test("the library offers a provider another member holds and not the member's own", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/connections": () =>
      json({
        connections: [
          {
            ...POOLED_NOTION.connections[0],
            shared: true,
            owner_email: "other@example.com",
          },
          grant("gmail", false, "g2"),
        ],
      }),
  });
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  await screen.findByLabelText("Notion connected");
  const available = within(zone("Available"));
  expect(available.getByText("Notion")).toBeTruthy();
  expect(available.queryByText("Gmail")).toBeNull();
});

test("the library offers every catalog tool and hoists the connected ones", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json(POOLED_NOTION) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Your connections" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Available" })).toBeTruthy();
  expect(connects("Slack")).toBeTruthy();
  expect(connects("Gmail")).toBeTruthy();
  expect(screen.getByLabelText("GitHub connected")).toBeTruthy();
  expect(screen.getByLabelText("Notion connected")).toBeTruthy();
  expect(within(zone("Available")).queryByText("Notion")).toBeNull();
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

test("a fully connected catalog stands as one zone", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/workspace/first-run": () =>
      json({ providers: [CATALOG.providers[2]], connectors: [] }),
    "/connections": () => json(POOLED_NOTION),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByRole("heading", { name: "Your connections" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Available" })).toBeNull();
});

test("the search narrows the catalog and states when nothing matches", async () => {
  location.hash = sectionHash("connectors");
  library();
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByRole("heading", { name: "Available" });

  await userEvent.type(screen.getByLabelText("Search connections"), "notion{Enter}");
  expect(connects("Notion")).toBeTruthy();
  expect(screen.queryByText("Slack")).toBeNull();

  await userEvent.clear(screen.getByLabelText("Search connections"));
  await userEvent.type(screen.getByLabelText("Search connections"), "salesforce{Enter}");
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

  await userEvent.type(screen.getByLabelText("Search connections"), "salesforce{Enter}");

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

test("a workspace install dispatches its own verb and hands back its install page", async () => {
  const posted: unknown[] = [];
  location.hash = sectionHash("connectors");
  library({
    "/actions/surface/slack$": () => json(SLACK_INSTALL_ACTIONS),
    "/actions/surface/slack/slack_connect": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "", url: SLACK_LINK });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("heading", { name: "Available" });
  await userEvent.click(connects("Slack"));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({});
  const link = await screen.findByRole("link", { name: "Open the Slack install page" });
  expect(link.getAttribute("href")).toBe(SLACK_LINK);
});

test("a refused intent states itself and the row stays open", async () => {
  location.hash = sectionHash("connectors");
  library({
    "/actions/surface/slack$": () => json(SLACK_INSTALL_ACTIONS),
    "/actions/surface/slack/slack_connect": () =>
      json({ applied: false, message: "Only a workspace admin connects Slack." }),
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
  expect(screen.queryByText("Notion")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Your connections" })).toBeNull();
  expect(screen.queryByText("Gmail")).toBeNull();
});

test("a connection outside the catalog stands only under every category", async () => {
  location.hash = sectionHash("connectors");
  library({ "/connections": () => json({ connections: [grant("sentry", false, "g9")] }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByLabelText("sentry connected");

  await pick("Category", "Communication");
  expect(screen.queryByLabelText("sentry connected")).toBeNull();
});

test("each biz-ops connector is drawn by a mark of its own", () => {
  for (const slug of ["apollo", "brex", "docusign", "mercury", "pandadoc", "ramp", "xero"]) {
    expect(BRAND_MARKS.has(slug) || slug in PROVIDER_GLYPHS).toBe(true);
  }
});
