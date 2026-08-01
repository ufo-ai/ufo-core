import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test } from "vitest";

import { Listing, type ListingSpec } from "@/kernel/listing";
import type { Placement } from "@/kernel/pager";
import { MainAgentProvider } from "@/lib/mainAgent";
import { ARTIFACTS } from "@/views/Artifacts";
import { SITES } from "@/views/Sites";
import { SOURCES } from "@/views/Sources";
import { Workspace } from "@/views/Workspace";
import { App } from "@/App";

import { AGENT, MEMBER, json, wire } from "./harness";

type Row = { name: string; count: number; note: string | null };

type Payload = { rows: Row[]; available?: boolean; newer?: string | null; older?: string | null };

const ROWS: Row[] = [
  { name: "alpha", count: 2, note: null },
  { name: "beta", count: 5, note: "seen" },
];

function spec(over: Partial<ListingSpec<Payload, Row>> = {}): ListingSpec<Payload, Row> {
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
          onPlace={(next) => {
            placed.push(next);
            setCurrent(next);
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
  expect(SITES.columns.map((column) => column.field)).toEqual(["name", "summary"]);
  expect(SOURCES.columns.map((column) => column.label)).toEqual([
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
  expect(await screen.findByText("Refused.")).toBeTruthy();
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
      <Workspace view="sites" />
    </MainAgentProvider>,
  );

  await waitFor(() => expect(headers()).toEqual(["site", "summary"]));
  expect(cellsOf("docs")).toEqual(["docs", "one page"]);
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
      <Workspace view="sources" />
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
      <Workspace view="sources" />
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
      <Workspace view="sources" />
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
      <Workspace view="sources" />
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
      <Workspace view="sources" />
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

test("the artifacts declaration binds its columns to the artifact payload", async () => {
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await waitFor(() => expect(headers()).toEqual(["file", "subject", "type", "size", "date"]));
  expect(cellsOf("report.txt")).toEqual([
    "report.txt",
    "member@example.com",
    "text/plain",
    "2 kB",
    "2026-08-01 06:00",
  ]);
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
