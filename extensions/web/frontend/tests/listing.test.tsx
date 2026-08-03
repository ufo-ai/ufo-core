import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, test } from "vitest";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import { MainAgentProvider } from "@/lib/mainAgent";
import { ARTIFACTS } from "@/views/Artifacts";
import { SITES } from "@/views/Sites";
import { SOURCES } from "@/views/Sources";
import { App } from "@/App";

import { AGENT, MEMBER, PlacedWorkspace, json, refusedNotice, wire } from "./harness";
type Row = { name: string; count: number; note: string | null };

beforeEach(() => {
});

type Payload = { rows: Row[]; available?: boolean; newer?: string | null; older?: string | null };

const ROWS: Row[] = [
  { name: "alpha", count: 2, note: null },
  { name: "beta", count: 5, note: "seen" },
];

type TableSpec = Extract<ListingSpec<Payload, Row>, { columns: unknown }>;

function spec(over: Partial<Omit<TableSpec, "list">> = {}): ListingSpec<Payload, Row> {
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

function mount(declaration: ListingSpec<Payload, Row>, place: Placement = {}) {
  const placed: Placement[] = [];
  function Harness() {
    const [current, setCurrent] = useState(place);
    return (
      <MainAgentProvider agents={[AGENT]}>
        <Listing
          spec={declaration}
          place={current}
          onPlace={(patch) => {
            placed.push(patch);
            setCurrent((held) => ({ ...held, ...patch }));
          }}
        />
      </MainAgentProvider>
    );
  }
  render(<Harness />);
  return placed;
}

function headers(): string[] {
  return screen.getAllByRole("columnheader").map((cell) => cell.textContent ?? "");
}

function disabled(name: string): boolean {
  return (screen.getByRole("button", { name }) as HTMLButtonElement).disabled;
}

function cellsOf(name: string): string[] {
  const row = screen.getByText(name).closest("tr");
  if (!row) throw new Error("no row for " + name);
  return [...row.querySelectorAll("td")].map((cell) => cell.textContent ?? "");
}

test("the declaration alone proves nothing — it is the renderer that must be pinned", () => {
  expect(SITES.list?.primary.field).toBe("name");
  expect(SOURCES.columns?.map((column) => column.label)).toEqual([
    "source",
    "streams",
    "owner",
    "access",
    "errors",
    "next sync",
  ]);
  expect(ARTIFACTS.paged).toBe(true);
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

test("a listing with no rows states its empty copy and lists no header", async () => {
  wire({ "/workspace/probe": () => json({ rows: [] }) });
  mount(spec());

  expect(await screen.findByText("Nothing listed yet.")).toBeTruthy();
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

test("an unavailable payload says so instead of showing the empty listing", async () => {
  wire({ "/workspace/probe": () => json({ rows: [], available: false }) });
  mount(spec({ unavailable: (payload) => (payload.available ? null : "Not installed.") }));

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
  await waitFor(() => expect(placed).toEqual([{ kind: undefined, after: "next" }]));
  await waitFor(() => expect(calls.some((url) => url.includes("after=next"))).toBe(true));
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
        <button type="button" disabled={busy} onClick={() => act({ verb: "poke", name: row.name })}>
          Poke {row.name}
        </button>
      ),
    }),
  );

  await waitFor(() => expect(headers()).toEqual(["who", "how many", "note", ""]));
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
        <button type="button" onClick={() => act({ verb: "poke", name: row.name })}>
          Poke {row.name}
        </button>
      ),
    }),
  );

  await waitFor(() => expect(screen.getByText("beta")).toBeTruthy());
  await userEvent.click(screen.getByRole("button", { name: "Poke beta" }));
  await waitFor(() => expect(placed).toEqual([{ notice: "Done." }]));
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
        <button type="button" disabled={busy} onClick={() => act({ verb: "poke", name: row.name })}>
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

test("a row opens its detail through the context and closes back to the listing", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(
    spec({
      columns: [
        {
          field: "name",
          label: "who",
          render: (name, row, { open }) => (
            <button type="button" onClick={() => open(row)}>
              {name}
            </button>
          ),
        },
        { field: "count", label: "how many" },
        { field: "note", label: "note" },
      ],
      detail: (row, close) => (
        <div>
          <span>detail of {row.name}</span>
          <button type="button" onClick={close}>
            Close
          </button>
        </div>
      ),
    }),
  );

  await userEvent.click(await screen.findByRole("button", { name: "beta" }));
  expect(screen.getByText("detail of beta")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByText("detail of beta")).toBeNull();
});

test("the sites declaration binds to the payload the workspace route answers", async () => {
  wire({
    "/workspace/sites": () =>
      json({ available: true, sites: [{ name: "docs", summary: "one page" }] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sites" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("docs"),
  );
  expect(item?.querySelector('[data-part="primary"]')?.textContent).toBe("docs");
  expect(item?.querySelector('[data-part="meta"]')?.textContent).toBe("one page");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

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
      "source",
      "streams",
      "owner",
      "access",
      "errors",
      "next sync",
      "",
    ]),
  );
  expect(cellsOf("databases, pages")).toEqual([
    "notion",
    "databases, pages",
    "member@example.com",
    "private",
    "3",
    "2026-08-01 06:00",
    "ResyncShareRemove",
  ]);
  expect(cellsOf("rss")).toEqual(["rss", "—", "—", "shared", "0", "2026-08-02 09:30", ""]);
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
  await waitFor(() => expect(bodies.length).toBe(2));
  expect(JSON.parse(bodies[1])).toEqual({
    verb: "delete",
    kind: "source",
    name: "notion-main",
  });
});

test("the artifacts declaration binds its parts to the artifact payload", async () => {
  wire({
    "/workspace/artifacts": () =>
      json({
        artifacts: [
          {
            filename: "report.txt",
            subject: "member@example.com",
            media_type: "text/plain",
            size_bytes: 2048,
            created_at: "2026-08-01T06:00:00",
            url: null,
          },
        ],
      }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("report.txt"),
  );
  expect(item?.querySelector('[data-part="primary"]')?.textContent).toBe("report.txt");
  expect(item?.querySelector('[data-part="meta"]')?.textContent).toBe(
    "member@example.com · text/plain · 2 kB",
  );
  expect(item?.querySelector('[data-part="when"]')?.textContent).toBe("2026-08-01 06:00");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
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
    "/intents": () => held,
  });
  render(<App agents={[AGENT]} member={MEMBER} />);

  location.hash = "#/workspace/sources";
  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));

  location.hash = "#/workspace/artifacts";
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
  const artifact = {
    filename: "report.txt",
    subject: null,
    media_type: "text/plain",
    size_bytes: 1,
    created_at: "2026-08-01T06:00:00",
    url: null,
  };
  expect(ARTIFACTS.rowKey(artifact)).toBe("2026-08-01T06:00:00|report.txt");
  expect(SITES.rowKey({ name: "docs", summary: "one page" })).toBe("docs");
  expect(
    SOURCES.rows({
      sources: [
        {
          name: "notion-main",
          backend: "notion",
          stream: "pages",
          account_id: "acct",
          base_url: null,
          owner_email: null,
          shared: false,
          consecutive_errors: 0,
          next_sync_at: "2026-08-01T06:00:00",
        },
      ],
    }).map(SOURCES.rowKey),
  ).toEqual(["notion-main"]);
});

test("refresh re-reads the listing in place", async () => {
  let served = 0;
  wire({
    "/workspace/probe": () => {
      served += 1;
      return json({ rows: served > 1 ? [{ name: "gamma", count: 1, note: null }] : ROWS });
    },
  });
  mount(spec());

  await screen.findByText("alpha");
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

  expect(await screen.findByText("gamma")).toBeTruthy();
  expect(screen.queryByText("alpha")).toBeNull();
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
  await userEvent.type(screen.getByRole("searchbox"), "bet");
  expect(screen.queryByText("alpha")).toBeNull();
  expect(screen.getByText("beta")).toBeTruthy();

  await userEvent.clear(screen.getByRole("searchbox"));
  await userEvent.type(screen.getByRole("searchbox"), "zzz");
  expect(await screen.findByText("Nothing matches.")).toBeTruthy();
  expect(screen.queryByText("Nothing listed yet.")).toBeNull();
  expect(screen.getByRole("searchbox")).toBeTruthy();
});

test("chips count the loaded rows live, filter on press, and toggle back off", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(spec({ chips: [{ label: "Noted", has: (row) => row.note !== null }] }));

  await userEvent.click(await screen.findByRole("button", { name: "Noted 1" }));
  expect(screen.queryByText("alpha")).toBeNull();
  expect(screen.getByText("beta")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Noted 1" }).getAttribute("aria-pressed")).toBe(
    "true",
  );

  await userEvent.click(screen.getByRole("button", { name: "Noted 1" }));
  expect(await screen.findByText("alpha")).toBeTruthy();
});

test("a pressed chip and a search term compose", async () => {
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
  await userEvent.click(screen.getByRole("button", { name: "Noted 2" }));
  await userEvent.type(screen.getByRole("searchbox"), "gam");

  expect(screen.queryByText("beta")).toBeNull();
  expect(screen.queryByText("alpha")).toBeNull();
  expect(screen.getByText("gamma")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Noted 1" })).toBeTruthy();
});

test("a chip's count reads the searched set, so its number equals what pressing it shows", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount(
    spec({
      search: (row) => row.name,
      chips: [{ label: "Noted", has: (row) => row.note !== null }],
    }),
  );

  await screen.findByText("alpha");
  await userEvent.type(screen.getByRole("searchbox"), "alpha");
  expect(screen.getByRole("button", { name: "Noted 0" })).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Noted 0" }));
  expect(await screen.findByText("Nothing matches.")).toBeTruthy();
});

test("refresh hides while loading and on an unavailable payload, and recovers a failed read", async () => {
  let served = 0;
  wire({
    "/workspace/probe": () => {
      served += 1;
      return served === 1 ? new Response("no", { status: 500 }) : json({ rows: ROWS });
    },
  });
  mount(spec());

  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("alpha")).toBeTruthy();
});

test("an unavailable listing offers no refresh, where re-reading cannot change the answer", async () => {
  wire({ "/workspace/probe": () => json({ rows: [], available: false }) });
  mount(spec({ unavailable: (payload) => (payload.available ? null : "Not installed.") }));

  expect(await screen.findByText("Not installed.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
});

test("an applied intent keeps the pressed chip and the typed search term", async () => {
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

  await userEvent.click(await screen.findByRole("button", { name: "Private 1" }));
  await userEvent.type(screen.getByRole("searchbox"), "notion");
  await userEvent.click(screen.getByRole("button", { name: "Resync" }));

  expect(await screen.findByText("Resync queued.")).toBeTruthy();
  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("notion");
  expect(screen.getByRole("button", { name: "Private 1" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
});

test("a row listing renders primary and separator-joined meta, skipping empty parts — no table", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount({
    read: "/workspace/probe",
    rows: (payload: Payload) => payload.rows,
    rowKey: (row: Row) => row.name,
    empty: "Nothing listed yet.",
    list: { primary: { field: "name" }, meta: [{ field: "note" }, { field: "count" }] },
  });

  const items = await screen.findAllByRole("listitem");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
  const beta = items.find((item) => item.textContent?.includes("beta"));
  expect(beta?.textContent).toContain("seen · 5");
  const alpha = items.find((item) => item.textContent?.includes("alpha"));
  expect(alpha?.textContent).not.toContain("·");
});

test("a row listing reads each part through its own render and keeps actions and detail", async () => {
  wire({ "/workspace/probe": () => json({ rows: ROWS }) });
  mount({
    read: "/workspace/probe",
    rows: (payload: Payload) => payload.rows,
    rowKey: (row: Row) => row.name,
    empty: "Nothing listed yet.",
    list: {
      primary: {
        field: "name",
        render: (name, row, { open }) => (
          <button type="button" onClick={() => open(row)}>
            {name}
          </button>
        ),
      },
      meta: [{ field: "note" }],
      when: { field: "count", render: (count) => "×" + String(count) },
    },
    actions: (row, { act }) => (
      <button type="button" onClick={() => act({ verb: "poke", name: row.name })}>
        Poke {row.name}
      </button>
    ),
    detail: (row, close) => (
      <div>
        <span>detail of {row.name}</span>
        <button type="button" onClick={close}>
          Close
        </button>
      </div>
    ),
  });

  expect(await screen.findByText("×5")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Poke alpha" })).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "beta" }));
  expect(screen.getByText("detail of beta")).toBeTruthy();
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

  await userEvent.click(await screen.findByRole("button", { name: "Shared 1" }));
  expect(screen.queryByText("notion")).toBeNull();
  expect(screen.getByText("rss")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Shared 1" }));
  await userEvent.type(screen.getByRole("searchbox"), "notion");
  expect(await screen.findByText("notion")).toBeTruthy();
  expect(screen.queryByText("rss")).toBeNull();
  expect(screen.getByRole("button", { name: "Private 1" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Shared 0" })).toBeTruthy();
});

test("a refresh keeps the controls row and the caretted search box while the re-read is in flight", async () => {
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
  await userEvent.type(screen.getByRole("searchbox"), "a");
  await userEvent.click(screen.getByRole("button", { name: "Refresh" }));

  expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("a");
  expect(screen.getByRole("button", { name: "Refresh" })).toBeTruthy();
  expect(document.activeElement).not.toBe(document.body);

  release(Response.json({ rows: ROWS }));
  expect(await screen.findByText("alpha")).toBeTruthy();
});

test("a chip alone survives an applied intent, with no search term typed", async () => {
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

  await userEvent.click(await screen.findByRole("button", { name: "Private 1" }));
  await userEvent.click(screen.getByRole("button", { name: "Resync" }));

  expect(await screen.findByText("Resync queued.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Private 1" }).getAttribute("aria-pressed")).toBe(
    "true",
  );
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
            consecutive_errors: 0,
            next_sync_at: "2026-08-02T09:30:00",
          },
        ],
      }),
    "/transcript": () => json({ messages: [] }),
    "/api/admin": () => new Response("no", { status: 404 }),
  });
  render(<App agents={[AGENT]} member={MEMBER} />);

  await userEvent.type(await screen.findByRole("searchbox"), "rss");
  location.hash = "#/agents/" + AGENT.id + "/chat";
  await waitFor(() => expect(screen.queryByRole("searchbox")).toBeNull());
  location.hash = "#/workspace/sources";

  expect(await screen.findByText("notion")).toBeTruthy();
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");

  await userEvent.type(screen.getByRole("searchbox"), "rss");
  location.hash = "#/admin";
  await waitFor(() => expect(screen.queryByRole("searchbox")).toBeNull());
  location.hash = "#/workspace/sources";

  expect(await screen.findByText("notion")).toBeTruthy();
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
});

