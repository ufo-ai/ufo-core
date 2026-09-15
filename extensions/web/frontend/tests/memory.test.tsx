import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";
import { friendlyMoment } from "@/lib/moments";

import { AGENT, PlacedWorkspace, json, refusedNotice, useStreamFake, wire } from "./harness";
const MATCH = {
  text: "the deploy runs on EKS",
  kind: "fact",
  ref: "memory/m1",
  created_at: "2026-07-20T08:00:00",
  subject: "shared",
};

const RECORD_CORRECTION = (maxLength?: number) => ({
  name: "record_correction",
  description: "Record a correction to a memory.",
  input_schema: {
    properties: {
      corrects: { type: "string", format: "uuid", title: "Corrects" },
      body: { type: "string", title: "Body", ...(maxLength ? { maxLength } : {}) },
    },
    required: ["corrects", "body"],
  },
  call: { kind: "memory", action: "record_correction", input: {} },
  label: "Record edit",
});

const memories = (matches: unknown[], rest: Record<string, unknown> = {}) => ({
  available: true,
  kinds: ["fact"],
  matches,
  actions: [RECORD_CORRECTION()],
  older: null,
  newer: null,
  ...rest,
});

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
  wire({
    "/workspace/memory": () => json({ available: false, kinds: [], matches: [], actions: [] }),
  });
  open();
  expect(await screen.findByText("This deploy has no memory extension.")).toBeTruthy();
});

test("the search stands with the filter on the band of controls, not on the name", async () => {
  wire({ "/workspace/memory": () => json(memories([MATCH])) });
  open();

  const header = document.querySelector<HTMLElement>('[data-slot="header"]')!;
  expect(header.contains(await screen.findByPlaceholderText("Search"))).toBe(false);
  expect(header.contains(await screen.findByRole("table"))).toBe(false);

  const filter = await screen.findByRole("tab", { name: "Fact" });
  const table = screen.getByRole("table");
  expect(header.contains(filter)).toBe(false);
  expect(filter.compareDocumentPosition(table) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryAllByRole("heading", { level: 2, name: "Memory" })).toEqual([]);
});

test("the listing filters by kind, and the filter rides the read", async () => {
  const { calls } = wire({
    "/workspace/memory": (url) =>
      json(
        memories(url.includes("kind=preference") ? [] : [MATCH], {
          kinds: ["fact", "preference"],
        }),
      ),
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
      json(memories(url.includes("q=eks") ? [MATCH] : [], { older: "older-cursor" })),
  });
  open();

  await screen.findByText("No memories yet.");
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();

  await userEvent.type(screen.getByPlaceholderText("Search"), "eks{Enter}");

  await waitFor(() => expect(calls.some((url) => url.includes("q=eks"))).toBe(true));
  expect(await screen.findByText("the deploy runs on EKS")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Fact" })).toBeNull();
});

test("the pager walks by the cursor the read returned", async () => {
  const { calls } = wire({
    "/workspace/memory": () =>
      json(memories([MATCH], { older: "older-cursor", newer: "newer-cursor" })),
  });
  open();

  await userEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(calls.some((url) => url.includes("after=older-cursor"))).toBe(true),
  );
});

test("a correction posts the projected write to the main agent's lane and re-reads on success", async () => {
  const posted: { url: string; body: unknown }[] = [];
  let reads = 0;
  wire({
    "/workspace/memory": () => {
      reads += 1;
      return json(memories([MATCH]));
    },
    "/record_correction": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "Recorded." });
    },
  });
  open();

  await userEvent.click(await screen.findByText("the deploy runs on EKS"));
  const field = screen.getByDisplayValue("the deploy runs on EKS");
  await userEvent.clear(field);
  await userEvent.type(field, "the deploy runs on EKS in us-west-2");
  await userEvent.click(screen.getByRole("button", { name: "Record edit" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    url: "/surface/web/agents/" + AGENT.id + "/actions/memory/record_correction",
    body: { corrects: "m1", body: "the deploy runs on EKS in us-west-2" },
  });
  await waitFor(() => expect(reads).toBe(2));
});

test("a correction opens in the sheet and the listing stays live behind it", async () => {
  wire({ "/workspace/memory": () => json(memories([MATCH])) });
  open();

  await userEvent.click(await screen.findByText("the deploy runs on EKS"));
  const sheet = document.querySelector<HTMLElement>('[data-slot="sheet-content"]')!;
  expect(within(sheet).getByText("The edit replaces this memory.")).toBeTruthy();
  expect(screen.getByRole("table")).toBeTruthy();
});

test("a memory carrying no memory ref is not a row the member can open", async () => {
  wire({
    "/workspace/memory": () => json(memories([{ ...MATCH, ref: "source/page-7" }])),
  });
  open();

  const row = (await screen.findByText("the deploy runs on EKS")).closest("tr")!;
  expect(row.getAttribute("role")).toBeNull();
  expect(row.getAttribute("tabindex")).toBeNull();
});

test("narrowing by kind keeps what the member has typed in the search box", async () => {
  wire({
    "/workspace/memory": () => json(memories([MATCH], { kinds: ["fact", "preference"] })),
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
    "/workspace/memory": () => json(memories([MATCH])),
    "/record_correction": () => json({ applied: false, message: "Only the owner corrects it." }),
  });
  open();

  await userEvent.click(await screen.findByText("the deploy runs on EKS"));
  await userEvent.click(screen.getByRole("button", { name: "Record edit" }));
  await refusedNotice("Only the owner corrects it.");
});

test("each memory is a row carrying its class and date, never its raw ref", async () => {
  const mine = { ...MATCH, text: "prefers terse answers", ref: null, subject: "member:m1" };
  const unfiled = { ...MATCH, text: "half-migrated note", ref: null, subject: null };
  wire({ "/workspace/memory": () => json(memories([MATCH, mine, unfiled])) });
  open();

  const heads = (await screen.findAllByRole("columnheader")).map((cell) => cell.textContent);
  expect(heads.slice(0, 4)).toEqual(["Memory", "Class", "Audience", "Added"]);

  const row = screen.getByText("the deploy runs on EKS").closest("tr") as HTMLTableRowElement;
  const cells = [...row.cells].map((cell) => cell.textContent);
  expect(cells.slice(0, 4)).toEqual([
    "the deploy runs on EKS",
    "Fact",
    "Workspace",
    friendlyMoment("2026-07-20T08:00:00", new Date()),
  ]);
  expect(row.textContent).not.toContain("memory/m1");

  const audience = (text: string) =>
    (screen.getByText(text).closest("tr") as HTMLTableRowElement).cells[2].textContent;
  expect(audience("prefers terse answers")).toBe("Only you");
  expect(audience("half-migrated note")).toBe("Unknown");
});

test("the correction field holds the bound the action's schema states", async () => {
  wire({
    "/workspace/memory": () => json(memories([MATCH], { actions: [RECORD_CORRECTION(40)] })),
  });
  open();

  await userEvent.click(await screen.findByText("the deploy runs on EKS"));
  const field = screen.getByDisplayValue("the deploy runs on EKS") as HTMLInputElement;
  expect(field.getAttribute("maxlength")).toBe("40");

  await userEvent.type(field, " in us-west-2, and also in eu-central-1");
  expect(field.value).toBe("the deploy runs on EKS in us-west-2, and");
});

test("a memory past the bound opens the correction empty beside the text it corrects", async () => {
  const overview =
    "The zephyr protocol handshake rotates every hour, and each rotation issues a fresh nonce.";
  const posted: string[] = [];
  wire({
    "/workspace/memory": () =>
      json(
        memories([{ ...MATCH, text: overview, kind: "semantic" }], {
          kinds: ["semantic"],
          actions: [RECORD_CORRECTION(40)],
        }),
      ),
    "/record_correction": (_url: string, init?: RequestInit) => {
      posted.push(JSON.parse(String(init?.body)).body);
      return json({ applied: true, message: "" });
    },
  });
  open();

  await userEvent.click(await screen.findByText(overview));
  const dialog = within(screen.getByRole("dialog"));
  expect(
    dialog.getByText(
      "This memory is too long to edit. Write the new statement.",
    ),
  ).toBeTruthy();
  expect(dialog.getByText(overview)).toBeTruthy();

  const field = dialog.getByRole("textbox", { name: "Body" }) as HTMLInputElement;
  expect(field.value).toBe("");
  expect(dialog.getByRole("button", { name: "Record edit" })).toHaveProperty("disabled", true);

  await userEvent.type(field, "The handshake rotates every hour.");
  await userEvent.click(dialog.getByRole("button", { name: "Record edit" }));
  await waitFor(() => expect(posted).toEqual(["The handshake rotates every hour."]));
  expect(posted[0].length).toBeLessThanOrEqual(40);
});
