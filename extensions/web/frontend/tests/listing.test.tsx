import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import { MainAgentProvider } from "@/lib/mainAgent";
import type { WorkspacePlace } from "@/lib/route";
import { SOURCES } from "@/views/Sources";
import { TabbedPane } from "@/views/TabbedPane";
import { App } from "@/App";

import {
  AGENT,
  MEMBER,
  PlacedWorkspace,
  SITE_KIND,
  json,
  objectIndex,
  refusedNotice,
  wire,
} from "./harness";
type Row = { name: string; count: number; note: string | null };

beforeEach(() => {});

type Payload = {
  rows: Row[];
  available?: boolean;
  newer?: string | null;
  older?: string | null;
};

const ROWS: Row[] = [
  { name: "alpha", count: 2, note: null },
  { name: "beta", count: 5, note: "seen" },
];

type TableSpec = Extract<ListingSpec<Payload, Row>, { columns: unknown }>;

function spec(
  over: Partial<Omit<TableSpec, "list">> = {},
): ListingSpec<Payload, Row> {
  return {
    read: "/workspace/probe",
    rows: (payload) => payload.rows,
    rowKey: (row) => row.name,
    columns: [
      { field: "name", label: "who" },
      { field: "count", label: "how many" },
      { field: "note", label: "note" },
    ],
    empty: "Nothing listed yet.",
    ...over,
  };
}

/** A listing is drawn under the shell that heads it, because the search it narrows on is the
 *  header's box and not its own. Mounting it bare would prove the narrowing against a control the
 *  member never touches. */
function mount(declaration: ListingSpec<Payload, Row>, place: Placement = {}) {
  const placed: Placement[] = [];
  const views = {
    probe: {
      label: "Probe",
      remountOnPlace: false,
      search: declaration.search || declaration.serverQuery ? "Search" : undefined,
      render: (current: Placement, onPlace: (patch: Placement) => void) => (
        <Listing
          spec={declaration}
          place={current}
          onPlace={(patch) => {
            placed.push(patch);
            onPlace(patch);
          }}
        />
      ),
    },
  };
  function Harness() {
    const [current, setCurrent] = useState<WorkspacePlace>(place);
    return (
      <MainAgentProvider agents={[AGENT]}>
        <TabbedPane
          group="probe"
          tabs={["probe"] as const}
          views={views}
          view="probe"
          place={current}
          onPlace={(_, next) => setCurrent(next)}
        />
      </MainAgentProvider>
    );
  }
  render(<Harness />);
  return placed;
}

async function narrow(term: string) {
  const box = screen.getByRole("searchbox");
  await userEvent.clear(box);
  await userEvent.type(box, term + "{enter}");
}

function headers(): string[] {
  return screen
    .getAllByRole("columnheader")
    .map((cell) => cell.textContent ?? "");
}

function disabled(name: string): boolean {
  return (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;
}

function cellsOf(name: string): string[] {
  return [...rowOf(name).querySelectorAll("td")].map((cell) => cell.textContent ?? "");
}

test("the declaration alone proves nothing — it is the renderer that must be pinned", () => {
  expect(SOURCES.columns?.map((column) => column.label)).toEqual([
    "Source",
    "Streams",
    "Owner",
    "Access",
    "Errors",
    "Next Sync",
  ]);
});

test("a listing renders the columns it declares, in order, reading the fields it names", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec());

  await waitFor(() => expect(headers()).toEqual(["who", "how many", "note"]));
  expect(cellsOf("alpha")).toEqual(["alpha", "2", ""]);
  expect(cellsOf("beta")).toEqual(["beta", "5", "seen"]);
});

test("a column's render receives the field's value, its row, and the row context", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(
    spec({
      columns: [
        { field: "name", label: "who" },
        {
          field: "count",
          label: "how many",
          render: (count, row) => "×" + String(count) + " of " + row.name,
        },
        { field: "note", label: "note" },
      ],
    }),
  );

  expect(await screen.findByText("×2 of alpha")).toBeTruthy();
  expect(screen.getByText("×5 of beta")).toBeTruthy();
});

test("a listing with no rows states its blank, and names itself nowhere", async () => {
  wire({ "/workspace/probe": () => json({ rows: [] }) });
  mount(spec({ search: (row: Row) => row.name }));

  expect(await screen.findByText("Nothing listed yet.")).toBeTruthy();
  expect(screen.queryByRole("heading", { level: 2, name: "Probe" })).toBeNull();
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

test("a listing's search stands on the band of controls, never on the name above them", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec({ search: (row: Row) => row.name }));

  await screen.findByText("alpha");
  const header = document.querySelector<HTMLElement>('[data-slot="header"]')!;
  const box = await screen.findByRole("searchbox");
  expect(header.contains(box)).toBe(false);
  expect(screen.getAllByRole("searchbox")).toHaveLength(1);
});

test("a card listing renders each face and its row action", async () => {
  const sentence = "This is a deliberately long sentence that should remain visible.";
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount({
    read: "/workspace/probe",
    rows: (payload) => payload.rows,
    rowKey: (row) => row.name,
    cards: {
      mark: { shape: "square" },
      primary: { field: "name" },
      status: { field: "count", render: (count) => String(count) },
      body: { field: "name", render: () => sentence },
    },
    empty: "Nothing listed yet.",
    actions: (row) => <button type="button">Act {row.name}</button>,
  });

  const grid = await screen.findByRole("list");
  expect(grid.className).toContain("grid-cols-2");
  const items = screen.getAllByRole("listitem");
  expect(items).toHaveLength(2);
  for (const item of items) {
    const mark = item.querySelector("[data-part=mark]");
    expect(mark?.getAttribute("aria-hidden")).toBe("true");
    expect(mark?.textContent).toBe("");
  }
  expect(screen.getByText("alpha")).toBeTruthy();
  expect(screen.getByText("2")).toBeTruthy();
  const body = screen.getAllByText(sentence)[0];
  expect(body.getAttribute("data-part")).toBe("body");
  expect(body.className).not.toContain("truncate");
  expect(screen.getAllByRole("button", { name: /Act/ })).toHaveLength(2);
});

test("an empty listing has no action", async () => {
  wire({ "/workspace/probe": () => json({ rows: [] }) });
  mount(spec());

  expect(await screen.findByText("Nothing listed yet.")).toBeTruthy();
  expect(screen.getByText("Nothing listed yet.").closest("button")).toBeNull();
});

test("an unavailable payload says so instead of showing the empty listing", async () => {
  wire({ "/workspace/probe": () => json({ rows: [], available: false }) });
  mount(
    spec({
      unavailable: (payload) => (payload.available ? null : "Not installed."),
    }),
  );

  expect(await screen.findByText("Not installed.")).toBeTruthy();
  expect(screen.queryByText("Nothing listed yet.")).toBeNull();
});

test("a paged listing carries the cursor into its read and steps to the next page", async () => {
  const calls: string[] = [];
  wire({
    "/workspace/probe": (url) => {
      calls.push(url);
      return json(
        url.includes("after=next")
          ? { rows: [{ name: "gamma", count: 1, note: null }] }
          : { rows: ROWS, older: "next" },
      );
    },
  });
  const placed = mount(spec({ paged: true }));

  await waitFor(() => expect(screen.getByText("alpha")).toBeTruthy());
  expect(calls[0].endsWith("/workspace/probe")).toBe(true);

  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  await waitFor(() =>
    expect(placed).toEqual([{ kind: undefined, after: "next" }]),
  );
  await waitFor(() =>
    expect(calls.some((url) => url.includes("after=next"))).toBe(true),
  );
  expect(await screen.findByText("gamma")).toBeTruthy();
});

test("an unpaged listing asks for no cursor even when the member holds a placement", async () => {
  const calls: string[] = [];
  wire({
    "/workspace/probe": (url) => {
      calls.push(url);
      return json({ rows: ROWS });
    },
  });
  mount(spec(), { after: "next" });

  await waitFor(() => expect(screen.getByText("alpha")).toBeTruthy());
  expect(calls.every((url) => !url.includes("after="))).toBe(true);
});

test("a declared action posts one intent on the main agent's lane and states a refusal in place", async () => {
  const posted: unknown[] = [];
  wire({
    "/workspace/probe": () => json({ rows: ROWS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: false, message: "Refused." });
    },
  });
  const placed = mount(
    spec({
      actions: (row, { act, busy }) => (
        <button
          type="button"
          disabled={busy}
          onClick={() => act({ verb: "poke", name: row.name })}
        >
          Poke {row.name}
        </button>
      ),
    }),
  );

  await waitFor(() =>
    expect(headers()).toEqual(["who", "how many", "note", ""]),
  );
  await userEvent.click(screen.getByRole("button", { name: "Poke beta" }));
  await waitFor(() => expect(posted).toEqual([{ verb: "poke", name: "beta" }]));
  await refusedNotice("Refused.");
  expect(placed).toEqual([]);
});

test("an applied action hands its outcome to the placement so the reloaded listing states it", async () => {
  wire({
    "/workspace/probe": () => json({ rows: ROWS }),
    "/intents": () => json({ applied: true, message: "Done." }),
  });
  const placed = mount(
    spec({
      actions: (row, { act }) => (
        <button
          type="button"
          onClick={() => act({ verb: "poke", name: row.name })}
        >
          Poke {row.name}
        </button>
      ),
    }),
  );

  await waitFor(() => expect(screen.getByText("beta")).toBeTruthy());
  await userEvent.click(screen.getByRole("button", { name: "Poke beta" }));
  await waitFor(() => expect(placed).toEqual([{ notice: "Done." }]));
});

test("a credential seam stores one slot and can reopen after closing", async () => {
  wire({
    "/workspace/probe": () => json({ rows: ROWS }),
    "/intents": () =>
      json({
        applied: false,
        message: "Credentials required.",
        credentials: {
          sealed: "sealed",
          reason: "Enter the workspace credential.",
          prompts: [{ slot: "token", prompt: "Token" }],
        },
      }),
  });
  const placed = mount(
    spec({
      actions: (row, { act }) => (
        <button
          type="button"
          onClick={() => act({ verb: "request", name: row.name })}
        >
          Request {row.name}
        </button>
      ),
      credentials: (request, onStored, close) => (
        <div>
          <div>{request.reason}</div>
          <button type="button" onClick={() => onStored(["token"])}>
            Store token
          </button>
          <button type="button" onClick={close}>
            Close
          </button>
        </div>
      ),
    }),
  );

  await userEvent.click(
    await screen.findByRole("button", { name: "Request beta" }),
  );
  expect(
    await screen.findByText("Enter the workspace credential."),
  ).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Store token" }));
  await waitFor(() => expect(placed).toEqual([{ notice: "Stored token." }]));

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByText("Enter the workspace credential.")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Request beta" }));
  expect(
    await screen.findByText("Enter the workspace credential."),
  ).toBeTruthy();
});

test("a credential renderer counts two stored slots", async () => {
  wire({
    "/workspace/probe": () => json({ rows: ROWS }),
    "/intents": () =>
      json({
        applied: false,
        message: "Credentials required.",
        credentials: {
          sealed: "sealed",
          reason: "Enter both workspace credentials.",
          prompts: [
            { slot: "client", prompt: "Client" },
            { slot: "secret", prompt: "Secret" },
          ],
        },
      }),
  });
  const placed = mount(
    spec({
      actions: (row, { act }) => (
        <button
          type="button"
          onClick={() => act({ verb: "request", name: row.name })}
        >
          Request {row.name}
        </button>
      ),
      credentials: (request, onStored) => (
        <div>
          <div>{request.reason}</div>
          <button type="button" onClick={() => onStored(["client", "secret"])}>
            Store credentials
          </button>
        </div>
      ),
    }),
  );

  await userEvent.click(
    await screen.findByRole("button", { name: "Request beta" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Store credentials" }),
  );
  await waitFor(() =>
    expect(placed).toEqual([{ notice: "2 credentials stored." }]),
  );
});

test("an action in flight disables its controls, so one click cannot post twice", async () => {
  let release: (value: Response) => void = () => {};
  const posted: unknown[] = [];
  wire({
    "/workspace/probe": () => json({ rows: ROWS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    },
  });
  mount(
    spec({
      actions: (row, { act, busy }) => (
        <button
          type="button"
          disabled={busy}
          onClick={() => act({ verb: "poke", name: row.name })}
        >
          Poke {row.name}
        </button>
      ),
    }),
  );

  const poke = await screen.findByRole("button", { name: "Poke beta" });
  await userEvent.click(poke);
  await waitFor(() => expect(posted.length).toBe(1));
  await waitFor(() => expect(disabled("Poke beta")).toBe(true));
  expect(disabled("Poke alpha")).toBe(true);

  release(Response.json({ applied: false, message: "Refused." }));
  await waitFor(() => expect(disabled("Poke beta")).toBe(false));
  expect(posted.length).toBe(1);
});

test("a listing that declares no action renders no trailing column", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec());

  await waitFor(() => expect(headers()).toEqual(["who", "how many", "note"]));
  expect(cellsOf("alpha").length).toBe(3);
});

function rowOf(name: string): HTMLElement {
  const row = screen.getAllByText(name).map((node) => node.closest("tr")).find(Boolean);
  if (!row) throw new Error("no row for " + name);
  return row;
}

test("the sources declaration projects a binding and a bare stream into one uniform table", async () => {
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 1,
            next_sync_at: "2026-08-01T06:00:00",
          },
          {
            name: "notion-main",
            backend: "notion",
            stream: "databases",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 2,
            next_sync_at: "2026-08-01T07:00:00",
          },
          {
            name: null,
            backend: "rss",
            stream: "feed",
            account_id: null,
            base_url: "https://example.com/feed",
            owner_email: null,
            shared: true,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-02T09:30:00",
          },
        ],
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await waitFor(() =>
    expect(headers()).toEqual([
      "Source",
      "Streams",
      "Owner",
      "Access",
      "Errors",
      "Next Sync",
      "",
    ]),
  );
  expect(cellsOf("databases, pages")).toEqual([
    "notion",
    "databases, pages",
    "member@example.com",
    "Only you",
    "3",
    "Aug 1 2026",
    "ResyncShareRemove",
  ]);
  expect(cellsOf("rss")).toEqual([
    "rss",
    "—",
    "Workspace",
    "Workspace",
    "0",
    "Aug 2 2026",
    "",
  ]);
});

test("a shared binding is offered no Share control", async () => {
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: true,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
        ],
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  expect(await screen.findByRole("button", { name: "Resync" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
});

test("resync posts the binding whole, account and base url included", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: "https://notion.example",
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
          {
            name: "notion-main",
            backend: "notion",
            stream: "databases",
            account_id: "acct",
            base_url: "https://notion.example",
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T07:00:00",
          },
        ],
      }),
    "/intents": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Resync queued." });
    },
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "apply",
    kind: "source",
    name: "notion-main",
    spec: {
      provider: "notion",
      streams: ["databases", "pages"],
      account_id: "acct",
      base_url: "https://notion.example",
      shared: false,
      resync: true,
    },
  });
});

test("an applied outcome states itself under the listing that produced it", async () => {
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
        ],
      }),
    "/intents": () => json({ applied: true, message: "Resync queued." }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));

  expect(await screen.findByText("Resync queued.")).toBeTruthy();
});

test("share flips the value it carries, and remove posts no spec at all", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
        ],
      }),
    "/intents": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Done." });
    },
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Share" }));
  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "apply",
    kind: "source",
    name: "notion-main",
    spec: {
      provider: "notion",
      streams: ["pages"],
      account_id: "acct",
      base_url: null,
      shared: true,
    },
  });

  await userEvent.click(await screen.findByRole("button", { name: "Remove" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Confirm remove" }),
  );
  await waitFor(() => expect(bodies.length).toBe(2));
  expect(JSON.parse(bodies[1])).toEqual({
    verb: "delete",
    kind: "source",
    name: "notion-main",
  });
});

test("an outcome released after the member left never resets the view they are on", async () => {
  let release: (value: Response) => void = () => {};
  const held = new Promise<Response>((resolve) => (release = resolve));
  const artifactReads: string[] = [];
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: null,
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
        ],
      }),
    "/workspace/artifacts": (url) => {
      artifactReads.push(url);
      return json({
        artifacts: [
          {
            id: "a1",
            filename: "report.txt",
            subject: null,
            media_type: "text/plain",
            size_bytes: 1,
            created_at: "2026-08-01T06:00:00",
            url: null,
          },
        ],
        older: "c1",
        newer: null,
      });
    },
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/intents": () => held,
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  location.hash = "#/workspace/sources";
  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));

  location.hash = "#/artifacts";
  await userEvent.click(await screen.findByRole("button", { name: "Older" }));
  await waitFor(() => expect(artifactReads.length).toBe(2));

  release(json({ applied: true, message: "Resync queued." }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(artifactReads).toEqual([
    "/surface/web/workspace/artifacts",
    "/surface/web/workspace/artifacts?after=c1",
  ]);
});

test("every declaration keys its rows on fields its own payload carries", () => {
  expect(
    SOURCES.rows({
      sources: [
        {
          name: "notion-main",
          backend: "notion",
          stream: "pages",
          account_id: "acct",
          base_url: null,
          backfill_days: null,
          owner_email: null,
          shared: false,
          own: false,
          consecutive_errors: 0,
          next_sync_at: "2026-08-01T06:00:00",
        },
      ],
    }).map(SOURCES.rowKey),
  ).toEqual(["notion-main"]);
});

test("a visible listing polls and a hidden listing does not", async () => {
  let served = 0;
  wire({
    "/workspace/probe": () => {
      served += 1;
      return json({
        rows: served > 1 ? [{ name: "gamma", count: 1, note: null }] : ROWS,
      });
    },
  });
  const visibility = Object.getOwnPropertyDescriptor(Document.prototype, "visibilityState");
  vi.useFakeTimers();
  try {
    mount(spec());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("alpha")).toBeTruthy();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(served).toBe(1);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText("gamma")).toBeTruthy();
    expect(screen.queryByText("alpha")).toBeNull();
  } finally {
    Reflect.deleteProperty(document, "visibilityState");
    if (visibility) Object.defineProperty(Document.prototype, "visibilityState", visibility);
    vi.useRealTimers();
  }
});

test("a listing that declares no search offers no search box", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec());

  await screen.findByText("alpha");
  expect(screen.queryByRole("searchbox")).toBeNull();
});

test("search narrows rows to matches and states when nothing matches", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec({ search: (row) => row.name }));

  await screen.findByText("alpha");
  await narrow("bet");
  expect(screen.queryByText("alpha")).toBeNull();
  expect(screen.getByText("beta")).toBeTruthy();

  await narrow("zzz");
  expect(await screen.findByText("Nothing matches.")).toBeTruthy();
  expect(screen.queryByText("Nothing listed yet.")).toBeNull();
  expect(screen.getByRole("searchbox")).toBeTruthy();
});

test("tabs filter rows, restore all, and move with arrow keys", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec({ chips: [{ label: "Noted", has: (row) => row.note !== null }] }));

  const tablist = await screen.findByRole("tablist", { name: "Filter" });
  expect(screen.getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe("true");
  await userEvent.click(screen.getByRole("tab", { name: "Noted" }));
  expect(screen.queryByText("alpha")).toBeNull();
  expect(screen.getByText("beta")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Noted" }).getAttribute("aria-selected")).toBe("true");

  await userEvent.click(screen.getByRole("tab", { name: "All" }));
  expect(await screen.findByText("alpha")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe("true");
  await userEvent.keyboard("{ArrowRight}");
  expect(screen.getByRole("tab", { name: "Noted" }).getAttribute("aria-selected")).toBe("true");
  expect(tablist).toBeTruthy();
});

test("a tab and a search term compose", async () => {
  wire({
    "/workspace/probe": () =>
      json({ rows: ROWS.concat({ name: "gamma", count: 7, note: "seen" }) }),
  });
  mount(
    spec({
      search: (row) => row.name,
      chips: [{ label: "Noted", has: (row) => row.note !== null }],
    }),
  );

  await screen.findByText("alpha");
  await userEvent.click(screen.getByRole("tab", { name: "Noted" }));
  await narrow("gam");

  expect(screen.queryByText("beta")).toBeNull();
  expect(screen.queryByText("alpha")).toBeNull();
  expect(screen.getByText("gamma")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Noted" })).toBeTruthy();
});

test("a tab remains available when its searched set is empty", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(
    spec({
      search: (row) => row.name,
      chips: [{ label: "Noted", has: (row) => row.note !== null }],
    }),
  );

  await screen.findByText("alpha");
  await narrow("alpha");
  expect(screen.getByRole("tab", { name: "Noted" })).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "Noted" }));
  expect(await screen.findByText("Nothing matches.")).toBeTruthy();
});

test("a failed listing states the error", async () => {
  let served = 0;
  wire({
    "/workspace/probe": () => {
      served += 1;
      return served === 1
        ? new Response("no", { status: 500 })
        : json({ rows: ROWS });
    },
  });
  mount(spec());

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
});

test("an unavailable listing states that it is not installed", async () => {
  wire({ "/workspace/probe": () => json({ rows: [], available: false }) });
  mount(
    spec({
      unavailable: (payload) => (payload.available ? null : "Not installed."),
    }),
  );

  expect(await screen.findByText("Not installed.")).toBeTruthy();
});

test("an applied intent keeps the selected tab and the typed search term", async () => {
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
        ],
      }),
    "/intents": () => json({ applied: true, message: "Resync queued." }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await userEvent.click(
    await screen.findByRole("tab", { name: "Only you" }),
  );
  await narrow("notion");
  await userEvent.click(screen.getByRole("button", { name: "Resync" }));

  expect(await screen.findByText("Resync queued.")).toBeTruthy();
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe(
    "notion",
  );
  expect(
    screen
      .getByRole("tab", { name: "Only you" })
      .getAttribute("aria-selected"),
  ).toBe("true");
});

test("a row listing renders primary and separator-joined meta, skipping empty parts — no table", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount({
    read: "/workspace/probe",
    rows: (payload: Payload) => payload.rows,
    rowKey: (row: Row) => row.name,
    empty: "Nothing listed yet.",
    list: {
      primary: { field: "name" },
      meta: [{ field: "note" }, { field: "count" }],
    },
  });

  const items = await screen.findAllByRole("listitem");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
  const beta = items.find((item) => item.textContent?.includes("beta"));
  expect(beta?.textContent).toContain("seen · 5");
  const alpha = items.find((item) => item.textContent?.includes("alpha"));
  expect(alpha?.textContent).not.toContain("·");
});

test("a row listing reads each part through its own render and keeps its actions", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount({
    read: "/workspace/probe",
    rows: (payload: Payload) => payload.rows,
    rowKey: (row: Row) => row.name,
    empty: "Nothing listed yet.",
    list: {
      primary: { field: "name", render: (name) => <span>{name}</span> },
      meta: [{ field: "note" }],
      when: { field: "count", render: (count) => "×" + String(count) },
    },
    actions: (row, { act }) => (
      <button
        type="button"
        onClick={() => act({ verb: "poke", name: row.name })}
      >
        Poke {row.name}
      </button>
    ),
  });

  expect(await screen.findByText("×5")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Poke alpha" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Poke beta" })).toBeTruthy();
});

test("the sources declaration searches and filters by access with live counts", async () => {
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
          {
            name: null,
            backend: "rss",
            stream: "feed",
            account_id: null,
            base_url: "https://example.com/feed",
            owner_email: null,
            shared: true,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-02T09:30:00",
          },
        ],
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await userEvent.click(
    await screen.findByRole("tab", { name: "Workspace" }),
  );
  expect(screen.queryByText("notion")).toBeNull();
  expect(screen.getByText("rss")).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "All" }));
  await narrow("notion");
  expect(await screen.findByText("notion")).toBeTruthy();
  expect(screen.queryByText("rss")).toBeNull();
  expect(screen.getByRole("tab", { name: "Only you" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Workspace" })).toBeTruthy();
});

test("a re-read keeps the controls row and the caret in the search box", async () => {
  let release: (value: Response) => void = () => {};
  let served = 0;
  wire({
    "/workspace/probe": () => {
      served += 1;
      if (served === 1) return json({ rows: ROWS });
      return new Promise<Response>((resolve) => {
        release = resolve;
      });
    },
  });
  mount(spec({ search: (row) => row.name }));

  await screen.findByText("alpha");
  await narrow("a");
  const read = screen.getByRole("searchbox");
  read.focus();
  expect((read as HTMLInputElement).value).toBe("a");

  expect(document.activeElement).not.toBe(document.body);

  release(Response.json({ rows: ROWS }));
  expect(await screen.findByText("alpha")).toBeTruthy();
});

test("a tab alone survives an applied intent, with no search term typed", async () => {
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
        ],
      }),
    "/intents": () => json({ applied: true, message: "Resync queued." }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sources" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("tab", { name: "Only you" }));
  await userEvent.click(screen.getByRole("button", { name: "Resync" }));

  expect(await screen.findByText("Resync queued.")).toBeTruthy();
  expect(
    screen
      .getByRole("tab", { name: "Only you" })
      .getAttribute("aria-selected"),
  ).toBe("true");
});

test("leaving the workspace entirely also releases held controls", async () => {
  location.hash = "#/workspace/sources";
  wire({
    "/workspace/sources": () =>
      json({
        sources: [
          {
            name: "notion-main",
            backend: "notion",
            stream: "pages",
            account_id: "acct",
            base_url: null,
            owner_email: "member@example.com",
            shared: false,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-01T06:00:00",
          },
          {
            name: null,
            backend: "rss",
            stream: "feed",
            account_id: null,
            base_url: "https://example.com/feed",
            owner_email: null,
            shared: true,
            own: true,
            consecutive_errors: 0,
            next_sync_at: "2026-08-02T09:30:00",
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
    "/api/admin": () => new Response("no", { status: 404 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await narrow("rss");
  location.hash = "#/agents/" + AGENT.id + "/chat";
  await waitFor(() => expect(screen.queryByPlaceholderText("Search sources")).toBeNull());
  location.hash = "#/workspace/sources";

  expect(await screen.findByText("notion")).toBeTruthy();
  expect(
    ((await screen.findByRole("searchbox")) as HTMLInputElement).value,
  ).toBe("");

  await narrow("rss");
  location.hash = "#/admin";
  await waitFor(() => expect(screen.queryByRole("searchbox")).toBeNull());
  location.hash = "#/workspace/sources";

  expect(await screen.findByText("notion")).toBeTruthy();
  expect(
    ((await screen.findByRole("searchbox")) as HTMLInputElement).value,
  ).toBe("");
});
