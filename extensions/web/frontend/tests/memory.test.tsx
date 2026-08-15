import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, PlacedWorkspace, json, refusedNotice, useStreamFake, wire } from "./harness";
const MATCH = {
  text: "the deploy runs on EKS",
  kind: "fact",
  ref: "memory/m1",
  created_at: "2026-07-20T08:00:00",
  subject: "shared",
};

function open() {
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="memory" />
    </MainAgentProvider>,
  );
}

beforeEach(() => {
  useStreamFake();
});

test("a deploy with no memory extension says so", async () => {
  wire({ "/workspace/memory": () => json({ available: false, kinds: [], matches: [] }) });
  open();
  expect(await screen.findByText("This deploy has no memory extension.")).toBeTruthy();
});

test("the search heads the page and the filter stands with the table", async () => {
  wire({
    "/workspace/memory": () =>
      json({ available: true, kinds: ["fact"], matches: [MATCH], older: null, newer: null }),
  });
  open();

  const header = screen.getByRole("heading", { level: 1, name: "Workspace" }).parentElement!;
  expect(header.contains(await screen.findByPlaceholderText("Search"))).toBe(true);
  expect(header.contains(screen.getByRole("table"))).toBe(false);

  const filter = await screen.findByRole("tab", { name: "Fact" });
  const table = screen.getByRole("table");
  expect(header.contains(filter)).toBe(false);
  expect(filter.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryAllByRole("heading", { level: 2, name: "Memory" })).toEqual([]);
});

test("the listing filters by kind, and the filter rides the read", async () => {
  const { calls } = wire({
    "/workspace/memory": (url) =>
      json({
        available: true,
        kinds: ["fact", "preference"],
        matches: url.includes("kind=preference") ? [] : [MATCH],
        older: null,
        newer: null,
      }),
  });
  open();

  expect(await screen.findByText("the deploy runs on EKS")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Preference" }));

  await waitFor(() => expect(calls.some((url) => url.includes("kind=preference"))).toBe(true));
  expect(await screen.findByText("No memories of this kind on this page.")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Preference" }).getAttribute("aria-selected")).toBe(
    "true",
  );
});

test("searching asks with the query and drops the filter and the pager", async () => {
  const { calls } = wire({
    "/workspace/memory": (url) =>
      json({
        available: true,
        kinds: ["fact"],
        matches: url.includes("q=eks") ? [MATCH] : [],
        older: "older-cursor",
        newer: null,
      }),
  });
  open();

  await screen.findByText("No memories yet.");
  expect(screen.queryByRole("button", { name: "Older" })).toBeNull();

  await userEvent.type(screen.getByPlaceholderText("Search"), "eks{Enter}");

  await waitFor(() => expect(calls.some((url) => url.includes("q=eks"))).toBe(true));
  expect(await screen.findByText("the deploy runs on EKS")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Older" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Fact" })).toBeNull();
});

test("the pager walks by the cursor the read returned", async () => {
  const { calls } = wire({
    "/workspace/memory": () =>
      json({
        available: true,
        kinds: ["fact"],
        matches: [MATCH],
        older: "older-cursor",
        newer: "newer-cursor",
      }),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Older" }));
  await waitFor(() =>
    expect(calls.some((url) => url.includes("after=older-cursor"))).toBe(true),
  );
});

test("a correction posts to the main agent's lane and re-reads on success", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  wire({
    "/workspace/memory": () => {
      reads += 1;
      return json({
        available: true,
        kinds: ["fact"],
        matches: [MATCH],
        older: null,
        newer: null,
      });
    },
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Recorded." });
    },
  });
  open();

  await userEvent.click(await screen.findByText("the deploy runs on EKS"));
  const field = screen.getByDisplayValue("the deploy runs on EKS");
  await userEvent.clear(field);
  await userEvent.type(field, "the deploy runs on EKS in us-west-2");
  await userEvent.click(screen.getByRole("button", { name: "Record correction" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "record",
    kind: "memory",
    corrects: "m1",
    body: "the deploy runs on EKS in us-west-2",
  });
  await waitFor(() => expect(reads).toBe(2));
});

test("a memory carrying no memory ref is not a row the member can open", async () => {
  wire({
    "/workspace/memory": () =>
      json({
        available: true,
        kinds: ["fact"],
        matches: [{ ...MATCH, ref: "source/page-7" }],
        older: null,
        newer: null,
      }),
  });
  open();

  const row = (await screen.findByText("the deploy runs on EKS")).closest("tr")!;
  expect(row.getAttribute("role")).toBeNull();
  expect(row.getAttribute("tabindex")).toBeNull();
});

test("narrowing by kind keeps what the member has typed in the search box", async () => {
  wire({
    "/workspace/memory": () =>
      json({
        available: true,
        kinds: ["fact", "preference"],
        matches: [MATCH],
        older: null,
        newer: null,
      }),
  });
  open();

  const box = await screen.findByPlaceholderText("Search");
  await userEvent.type(box, "half-written");
  await userEvent.click(await screen.findByRole("tab", { name: "Preference" }));

  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Preference" }).getAttribute("aria-selected")).toBe(
      "true",
    ),
  );
  expect((screen.getByPlaceholderText("Search") as HTMLInputElement).value).toBe(
    "half-written",
  );
});

test("a refused correction tones its notice in place", async () => {
  wire({
    "/workspace/memory": () =>
      json({ available: true, kinds: ["fact"], matches: [MATCH], older: null, newer: null }),
    "/intents": () => json({ applied: false, message: "Only the owner corrects it." }),
  });
  open();

  await userEvent.click(await screen.findByText("the deploy runs on EKS"));
  await userEvent.click(screen.getByRole("button", { name: "Record correction" }));
  await refusedNotice("Only the owner corrects it.");
});

test("each memory is a row carrying its class and date, never its raw ref", async () => {
  const mine = { ...MATCH, text: "prefers terse answers", ref: null, subject: "member:m1" };
  const unfiled = { ...MATCH, text: "half-migrated note", ref: null, subject: null };
  wire({
    "/workspace/memory": () =>
      json({
        available: true,
        kinds: ["fact"],
        matches: [MATCH, mine, unfiled],
        older: null,
        newer: null,
      }),
  });
  open();

  const heads = (await screen.findAllByRole("columnheader")).map((cell) => cell.textContent);
  expect(heads.slice(0, 4)).toEqual(["Memory", "Class", "Audience", "Added"]);

  const row = screen.getByText("the deploy runs on EKS").closest("tr") as HTMLTableRowElement;
  const cells = [...row.cells].map((cell) => cell.textContent);
  expect(cells.slice(0, 4)).toEqual([
    "the deploy runs on EKS",
    "Fact",
    "Workspace",
    "Jul 20 2026",
  ]);
  expect(row.textContent).not.toContain("memory/m1");

  const audience = (text: string) =>
    (screen.getByText(text).closest("tr") as HTMLTableRowElement).cells[2].textContent;
  expect(audience("prefers terse answers")).toBe("Only you");
  expect(audience("half-migrated note")).toBe("Unknown");
});
