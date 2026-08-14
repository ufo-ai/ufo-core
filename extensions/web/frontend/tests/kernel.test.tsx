import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";

import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { Pager } from "@/kernel/pager";
import { RowLines } from "@/kernel/rows";
import { getJson } from "@/lib/api";
import { Notice, OutcomeNotice, Panel, QUIET, Section, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { Table, Td, Th } from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import type { SchemaProperty } from "@/lib/types";

import { json, opened } from "./harness";

type Row = { name: string };

function Listing({ path }: { path: string }) {
  const state = usePanelRead<{ rows: Row[] }>(path);
  return (
    <Panel state={state} empty={(payload) => (payload.rows.length ? null : "Nothing listed.")}>
      {(payload) => (
        <DataTable
          columns={["name"]}
          rows={payload.rows}
          rowKey={(row) => row.name}
          empty="unreachable"
        >
          {(row) => <Td>{row.name}</Td>}
        </DataTable>
      )}
    </Panel>
  );
}

test("the fence discards a read the member superseded while the view stayed mounted", async () => {
  const pending = new Map<string, (value: Response) => void>();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (url: string) =>
        new Promise<Response>((resolve) => {
          pending.set(url, resolve);
        }),
    ),
  );

  function Switcher() {
    const [page, setPage] = useState("/first");
    return (
      <>
        <button type="button" onClick={() => setPage("/second")}>
          go
        </button>
        <Listing path={page} />
      </>
    );
  }

  render(<Switcher />);
  await waitFor(() => expect(pending.has("/surface/web/first")).toBe(true));

  await userEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(pending.has("/surface/web/second")).toBe(true));

  pending.get("/surface/web/second")!(json({ rows: [{ name: "chosen" }] }));
  expect(await screen.findByText("chosen")).toBeTruthy();

  pending.get("/surface/web/first")!(json({ rows: [{ name: "stale" }] }));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("stale")).toBeNull();
  expect(screen.getByText("chosen")).toBeTruthy();
});

test("the fence aborts the request the member left behind", async () => {
  const signals: AbortSignal[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url: string, init?: RequestInit) =>
        new Promise<Response>(() => {
          if (init?.signal) signals.push(init.signal);
        }),
    ),
  );

  function Switcher() {
    const [page, setPage] = useState("/first");
    return (
      <>
        <button type="button" onClick={() => setPage("/second")}>
          go
        </button>
        <Listing path={page} />
      </>
    );
  }

  render(<Switcher />);
  await waitFor(() => expect(signals.length).toBe(1));
  expect(signals[0].aborted).toBe(false);

  await userEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(signals.length).toBe(2));

  expect(signals[0].aborted).toBe(true);
  expect(signals[1].aborted).toBe(false);
});

test("the fence answers a failed read with the message and no rows", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status: 500 })));
  render(<Listing path="/rows" />);
  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the fence answers an empty payload with its own words, not an empty table", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ rows: [] })));
  render(<Listing path="/rows" />);
  expect(await screen.findByText("Nothing listed.")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the table names its columns and one row per record", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ rows: [{ name: "one" }, { name: "two" }] })));
  render(<Listing path="/rows" />);
  await screen.findByText("one");
  expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["name"]);
  expect(screen.getAllByRole("row")).toHaveLength(3);
});

test("the table falls to its own empty words when it holds no rows", async () => {
  render(
    <DataTable columns={["name"]} rows={[]} rowKey={() => ""} empty="No rows here.">
      {() => <Td />}
    </DataTable>,
  );
  expect(screen.getByText("No rows here.")).toBeTruthy();
  expect(screen.queryByRole("table")).toBeNull();
});

test("the table states a floor covering every track it declares", async () => {
  const columns = [
    { label: "Provider", fact: true },
    "Account",
    "Owner",
    { label: "Access", fact: true },
    { label: "Connected", fact: true },
    "",
  ];
  const { unmount } = render(
    <DataTable columns={columns} rows={[{ name: "one" }]} rowKey={() => "one"} empty="none">
      {() => <Td />}
    </DataTable>,
  );
  expect(screen.getByRole("table").style.minWidth).toBe(
    "calc(3 * var(--size-fact-column) + 3 * var(--size-prose-column) + 0 * var(--size-act))",
  );
  unmount();

  render(
    <DataTable
      columns={[{ label: "Schedule", fact: true }, "Name"]}
      rows={[{ name: "one" }]}
      rowKey={() => "one"}
      empty="none"
      act={() => "Open"}
    >
      {() => <Td />}
    </DataTable>,
  );
  expect(screen.getByRole("table").style.minWidth).toBe(
    "calc(1 * var(--size-fact-column) + 1 * var(--size-prose-column) + 1 * var(--size-act))",
  );
});

test("the pager offers only the directions the payload carries", async () => {
  const placed: unknown[] = [];
  const { unmount } = render(
    <Pager payload={{ older: "cursor-older" }} onPlace={(next) => placed.push(next)} />,
  );
  expect(screen.queryByRole("button", { name: "Newer" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  expect(placed).toEqual([{ after: "cursor-older" }]);
  unmount();

  render(<Pager payload={{ newer: "n", older: "o" }} onPlace={() => {}} />);
  expect(screen.getByRole("button", { name: "Newer" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Older" })).toBeTruthy();
});

function Form({ schema }: { schema: Parameters<typeof FormFromSchema>[0]["schema"] }) {
  const [values, setValues] = useState<Record<string, SpecValue>>({});
  return (
    <FormFromSchema
      schema={schema}
      values={values}
      onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
    />
  );
}

test("a schema field with an enum becomes a select over exactly its choices", async () => {
  render(
    <Form
      schema={{ properties: { reasoning: { type: "string", enum: ["low", "high"] } } }}
    />,
  );
  expect((await opened("reasoning")).map((option) => option.textContent)).toEqual([
    "low",
    "high",
  ]);
});

test("a schema field is labelled by its title and states nothing the schema wrote for the agent", () => {
  const wire = {
    type: "string",
    title: "Expires At",
    description: "UTC expiry. Omit on update to preserve it; null clears it.",
  } as SchemaProperty;
  render(<Form schema={{ properties: { expires_at: wire } }} />);
  expect(screen.getByLabelText("Expires At")).toBeTruthy();
  expect(screen.queryByText("expires_at")).toBeNull();
  expect(screen.queryByText(/Omit on update/)).toBeNull();
});

test("a schema field's example is the placeholder, so the shape is shown, not described", () => {
  render(
    <Form
      schema={{
        properties: {
          schedule: {
            title: "Schedule",
            examples: ["0 9 * * 1-5"],
            anyOf: [{ type: "string", maxLength: 100 }, { type: "null" }],
          },
        },
      }}
    />,
  );
  const box = screen.getByLabelText("Schedule") as HTMLInputElement;
  expect(box.placeholder).toBe("0 9 * * 1-5");
  expect(box.maxLength).toBe(100);
});

test("a moment the schema declares takes the browser's own date control", () => {
  render(
    <Form
      schema={{
        properties: {
          expires_at: {
            title: "Expires At",
            anyOf: [{ type: "string", format: "date-time" }, { type: "null" }],
          },
        },
      }}
    />,
  );
  expect(screen.getByLabelText("Expires At").getAttribute("type")).toBe("datetime-local");
});

test("a string the schema declines to bound is prose and takes the taller box", () => {
  render(
    <Form
      schema={{
        properties: {
          prompt: {
            title: "Prompt",
            examples: ["Summarize what merged."],
            anyOf: [{ type: "string" }, { type: "null" }],
          },
          description: {
            title: "Description",
            anyOf: [{ type: "string", maxLength: 120 }, { type: "null" }],
          },
        },
      }}
    />,
  );
  expect(screen.getByLabelText("Prompt").tagName).toBe("TEXTAREA");
  expect((screen.getByLabelText("Prompt") as HTMLTextAreaElement).placeholder).toBe(
    "Summarize what merged.",
  );
  expect(screen.getByLabelText("Description").tagName).toBe("INPUT");
});

test("a schema field with no title falls back to the key the spec names it by", () => {
  render(<Form schema={{ properties: { expires_at: { type: "string" } } }} />);
  expect(screen.getByLabelText("expires_at")).toBeTruthy();
});

test("a boolean field carries its label beside the box, not stacked over it", () => {
  render(<Form schema={{ properties: { paused: { type: "boolean", title: "Paused" } } }} />);
  const box = screen.getByLabelText("Paused");
  expect(box.getAttribute("type")).toBe("checkbox");
  expect(box.parentElement?.className).toContain("items-center");
});

test("a nullable boolean field becomes a checkbox, not a text box", () => {
  render(
    <Form schema={{ properties: { paused: { anyOf: [{ type: "boolean" }, { type: "null" }] } } }} />,
  );
  expect(screen.getByLabelText("paused").getAttribute("type")).toBe("checkbox");
});

test("a boolean field already true arrives checked, not as the string true", () => {
  const prop = { anyOf: [{ type: "boolean" }, { type: "null" }] };
  function Seeded() {
    const [values, setValues] = useState<Record<string, SpecValue>>({
      paused: initialSpecValue(prop, true),
    });
    return (
      <FormFromSchema
        schema={{ properties: { paused: prop } }}
        values={values}
        onChange={(name, value) => setValues((current) => ({ ...current, [name]: value }))}
      />
    );
  }
  render(<Seeded />);
  expect((screen.getByLabelText("paused") as HTMLInputElement).checked).toBe(true);
});

test("a schema field labels itself with the key the member writes in chat", () => {
  render(<Form schema={{ properties: { expires_at: { type: "string" } } }} />);
  expect(screen.getByLabelText("expires_at")).toBeTruthy();
});

test("a read whose body fails mid-stream reports the failure instead of rejecting", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        ({
          ok: true,
          status: 200,
          json: async () => {
            throw new DOMException("The operation was aborted.", "AbortError");
          },
        }) as unknown as Response,
    ),
  );
  await expect(getJson("/whatever")).resolves.toEqual({
    ok: false,
    message: "Network error — try again.",
    status: 0,
  });
});

test("a loading panel draws the shape it is about to fill, not the payload", () => {
  const { container } = render(
    <Panel state={{ phase: "loading" }} shape="cards">{() => <div>never</div>}</Panel>,
  );
  expect(container.querySelectorAll('[data-part="skeleton"]').length).toBeGreaterThan(0);
  expect(screen.queryByText("never")).toBeNull();
});

test("an attention notice is highlighted and a quiet one is not", () => {
  const { container } = render(
    <div>
      <Notice tone="attention">refused</Notice>
      <Notice>done</Notice>
    </div>,
  );
  const [attention, quiet] = Array.from(container.querySelectorAll("div > div > div"));
  expect(attention.className).toContain("bg-attention");
  expect(attention.className).toContain("[color:var(--color-attention-ink)]");
  expect(attention.className).toContain("text-ui");
  expect(attention.getAttribute("role")).toBe("status");
  expect(quiet.className).not.toContain("bg-attention");
  expect(quiet.getAttribute("role")).toBe("status");
});

test("a quiet outcome takes no room until there is an outcome to state", () => {
  const { container, rerender } = render(<OutcomeNotice state={QUIET} />);
  expect(container.firstChild).toBeNull();

  rerender(<OutcomeNotice state={{ text: "Applied.", refused: false }} />);
  expect(screen.getByRole("status").textContent).toBe("Applied.");
});

test("a schema field the model requires says so, and an optional one beside it does not", () => {
  render(
    <Form
      schema={{
        properties: { prompt: { type: "string" }, expires_at: { type: "string" } },
        required: ["prompt"],
      }}
    />,
  );
  expect((screen.getByLabelText("prompt") as HTMLInputElement).required).toBe(true);
  expect((screen.getByLabelText("expires_at") as HTMLInputElement).required).toBe(false);
});

test("a section stacks heading, action bar, then records, and the bar runs from the left", () => {
  render(
    <Section
      title="Members"
      bar={
        <>
          <input aria-label="Search members" />
          <button type="button">Add member</button>
        </>
      }
    >
      roster
    </Section>,
  );
  const heading = screen.getByRole("heading", { name: "Members" });
  const search = screen.getByLabelText("Search members");
  const action = screen.getByRole("button", { name: "Add member" });
  const bar = search.parentElement!;
  expect(bar).toBe(action.parentElement);
  expect(bar.className).not.toContain("justify-between");
  expect(bar.previousElementSibling).toBe(heading.closest("div")?.parentElement);
});

test("a section's action stands on the heading line, over the bar and the records", () => {
  render(
    <Section title="Members" action={<a href="https://example.test">Elsewhere</a>} bar={<button type="button">Add member</button>}>
      roster
    </Section>,
  );
  const heading = screen.getByRole("heading", { name: "Members" });
  const out = screen.getByRole("link", { name: "Elsewhere" });
  expect(heading.nextElementSibling).toBe(out);
  expect(out.parentElement).toBe(heading.parentElement);
});

test("a section's note stands under its heading, not under the bar", () => {
  render(
    <Section title="Credentials" note="A slot is filled in chat." bar={<button type="button">Refresh</button>}>
      rows
    </Section>,
  );
  const heading = screen.getByRole("heading", { name: "Credentials" });
  const note = screen.getByText("A slot is filled in chat.");
  expect(heading.parentElement?.nextElementSibling).toBe(note);
  expect(note.parentElement).toBe(heading.parentElement?.parentElement);
});

test("a table stands on the section's own ground, ruled only between its records", () => {
  render(
    <Table>
      <thead>
        <tr>
          <Th>Member</Th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <Td>lead@example.com</Td>
        </tr>
      </tbody>
    </Table>,
  );
  const frame = screen.getByRole("table").parentElement;
  expect(frame?.className).not.toContain("border-edge");
  expect(frame?.className).not.toContain("rounded-panel");
  const head = screen.getByRole("columnheader", { name: "Member" });
  expect(head.className).not.toContain("border");
  const cell = screen.getByRole("cell", { name: "lead@example.com" });
  expect(cell.className).toContain("border-b");
  expect(cell.className).not.toContain("border-t");
});

test("a row is a band: the cells sit inside the rule that states the row's extent", () => {
  render(
    <Table>
      <tbody>
        <tr>
          <Td>lead@example.com</Td>
        </tr>
      </tbody>
    </Table>,
  );
  const cell = screen.getByRole("cell", { name: "lead@example.com" });
  expect(cell.className).toContain("px-2xl");
  expect(cell.className).toContain("truncate");
});

test("a section with nothing to act on draws no action bar", () => {
  render(<Section title="Members">roster</Section>);
  expect(screen.getByRole("heading", { name: "Members" }).nextElementSibling).toBeNull();
});

test("a section with no action draws no action slot", () => {
  render(<Section title="Members">roster</Section>);
  expect(screen.queryByRole("button")).toBeNull();
});

test("a row's own act fires without opening the row it stands on", async () => {
  const opened: string[] = [];
  const acted: string[] = [];
  render(
    <RowLines
      rows={[{ name: "one" }, { name: "two" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      meta={() => []}
      open={(row) => () => opened.push(row.name)}
      action={(row) => (
        <Button variant="row" onClick={() => acted.push(row.name)}>
          Remove
        </Button>
      )}
    />,
  );

  await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
  expect(acted).toEqual(["one"]);
  expect(opened).toEqual([]);

  await userEvent.click(screen.getByRole("button", { name: /^two/ }));
  expect(opened).toEqual(["two"]);
});

test("a row the listing declines to open takes no role and no tab stop", async () => {
  const opened: string[] = [];
  render(
    <RowLines
      rows={[{ name: "shared" }, { name: "walled" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      meta={() => []}
      open={(row) => (row.name === "walled" ? null : () => opened.push(row.name))}
    />,
  );

  expect(screen.getAllByRole("button").map((row) => row.textContent)).toEqual(["shared"]);
  const walled = screen.getByText("walled").closest("li")!;
  expect(walled.getAttribute("tabindex")).toBeNull();
  expect(walled.className).not.toContain("cursor-pointer");

  await userEvent.click(walled);
  expect(opened).toEqual([]);
});

test("the keyboard opens a row on Enter and on Space", async () => {
  const opened: string[] = [];
  render(
    <RowLines
      rows={[{ name: "one" }]}
      rowKey={(row) => row.name}
      primary={(row) => row.name}
      meta={() => []}
      open={(row) => () => opened.push(row.name)}
    />,
  );

  const row = screen.getByRole("button", { name: "one" });
  row.focus();
  expect(document.activeElement).toBe(row);
  await userEvent.keyboard("{Enter}");
  await userEvent.keyboard(" ");

  expect(opened).toEqual(["one", "one"]);
});
