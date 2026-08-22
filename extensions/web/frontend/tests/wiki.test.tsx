import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, PlacedSection, json, useStreamFake, wire } from "./harness";

const QUEUED =
  "The facts derived from synced pages are written again as the derivation pass reaches each " +
  "page. Overview summaries and items an app recorded in a conversation are untouched.";

function open() {
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="wiki" />
    </MainAgentProvider>,
  );
}

function page() {
  return {
    "/workspace/memory": () =>
      json({ available: true, kinds: ["semantic"], matches: [], body_max_chars: 115 }),
    "/objects/member": () => json({ objects: [], after: null }),
    "/objects/memory": () => json({ objects: [], after: null }),
  };
}

async function openTheDialog() {
  await userEvent.click(await screen.findByRole("button", { name: "Page actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Rebuild page facts" }));
}

beforeEach(() => {
  useStreamFake();
});

test("the page states what a rebuild leaves alone before it is pressed", async () => {
  wire(page());
  open();
  await openTheDialog();

  await screen.findByRole("heading", { name: "Rebuild Page Facts" });
  expect(screen.getByText(/derived from synced pages are written again/)).toBeTruthy();
  expect(screen.getByText(/Overview summaries are not rebuilt/)).toBeTruthy();
  expect(screen.getByText(/recorded in a conversation are not rebuilt/)).toBeTruthy();
});

test("the rebuild rides the main agent's intent lane and states what it queued", async () => {
  const posted: unknown[] = [];
  wire({
    ...page(),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: QUEUED });
    },
  });
  open();
  await openTheDialog();
  await userEvent.click(screen.getByRole("button", { name: "Rebuild page facts" }));

  await waitFor(() => expect(posted).toEqual([{ verb: "rebuild_page_facts" }]));
  expect(await screen.findByText(QUEUED)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Rebuild page facts" })).toBeNull();
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
});

test("a refused rebuild is read back in place, and the act stands", async () => {
  wire({
    ...page(),
    "/intents": () =>
      json({
        applied: false,
        message: "Only a workspace admin can rebuild the facts derived from synced pages.",
      }),
  });
  open();
  await openTheDialog();
  await userEvent.click(screen.getByRole("button", { name: "Rebuild page facts" }));

  const notice = await screen.findByText(
    "Only a workspace admin can rebuild the facts derived from synced pages.",
  );
  expect(notice.className).toContain("bg-attention");
  expect(screen.getByRole("button", { name: "Rebuild page facts" })).toBeTruthy();
});

test("reloading the page re-reads it and admits no turn", async () => {
  let reads = 0;
  const { calls } = wire({
    ...page(),
    "/workspace/memory": () => {
      reads += 1;
      return json({ available: true, kinds: ["semantic"], matches: [], body_max_chars: 115 });
    },
  });
  open();
  await waitFor(() => expect(reads).toBe(1));

  await userEvent.click(await screen.findByRole("button", { name: "Page actions" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Reload" }));

  await waitFor(() => expect(reads).toBe(2));
  expect(calls.some((url) => url.includes("/intents"))).toBe(false);
});
