import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";

import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { Pager } from "@/kernel/pager";
import { getJson } from "@/lib/api";
import { Panel, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { Td } from "@/components/ui/table";

import { json } from "./harness";

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

test("the pager offers only the directions the payload carries", async () => {
  const placed: unknown[] = [];
  const { unmount } = render(
    <Pager payload={{ older: "cursor-older" }} place={{}} onPlace={(next) => placed.push(next)} />,
  );
  expect(screen.queryByRole("button", { name: "Newer" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  expect(placed).toEqual([{ kind: undefined, after: "cursor-older" }]);
  unmount();

  render(<Pager payload={{ newer: "n", older: "o" }} place={{ kind: "fact" }} onPlace={() => {}} />);
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
  const control = screen.getByLabelText("reasoning");
  expect(control.tagName).toBe("SELECT");
  expect([...(control as HTMLSelectElement).options].map((option) => option.value)).toEqual([
    "low",
    "high",
  ]);
});

test("a schema field states its name and nothing the schema wrote for the agent", () => {
  render(
    <Form
      schema={{
        properties: {
          expires_at: {
            type: "string",
            description: "UTC expiry. Omit on update to preserve it; null clears it.",
          },
        },
      }}
    />,
  );
  expect(screen.getByLabelText("expires_at")).toBeTruthy();
  expect(screen.queryByText(/Omit on update/)).toBeNull();
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
  });
});
