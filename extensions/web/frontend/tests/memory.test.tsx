import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";
import { Workspace } from "@/views/Workspace";

import { AGENT, json, useStreamFake, wire } from "./harness";

const MATCH = {
  text: "the deploy runs on EKS",
  kind: "fact",
  ref: "memory/m1",
  created_at: "2026-07-20T08:00:00",
};

function open(view: "memory" = "memory") {
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <Workspace view={view} />
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
  await userEvent.click(screen.getByRole("button", { name: "preference" }));

  await waitFor(() => expect(calls.some((url) => url.includes("kind=preference"))).toBe(true));
  expect(await screen.findByText("No preference memories on this page.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "preference" }).getAttribute("aria-current")).toBe(
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
  expect(screen.getByRole("button", { name: "Older" })).toBeTruthy();

  await userEvent.type(screen.getByPlaceholderText("Search memory…"), "eks");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(calls.some((url) => url.includes("q=eks"))).toBe(true));
  expect(await screen.findByText("the deploy runs on EKS")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Older" })).toBeNull();
  expect(screen.queryByRole("button", { name: "fact" })).toBeNull();
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

  await userEvent.click(await screen.findByRole("button", { name: "Correct" }));
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

test("a memory carrying no memory ref offers no correction", async () => {
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

  expect(await screen.findByText("source/page-7")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Correct" })).toBeNull();
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

  const box = await screen.findByPlaceholderText("Search memory…");
  await userEvent.type(box, "half-written");
  await userEvent.click(await screen.findByRole("button", { name: "preference" }));

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "preference" }).getAttribute("aria-current")).toBe(
      "true",
    ),
  );
  expect((screen.getByPlaceholderText("Search memory…") as HTMLInputElement).value).toBe(
    "half-written",
  );
});
