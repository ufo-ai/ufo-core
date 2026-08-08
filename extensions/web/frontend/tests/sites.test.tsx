import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, PlacedWorkspace, SITE_KIND, objectIndex, useStreamFake, wire } from "./harness";

const DOCS = {
  name: "docs-abc",
  summary: "docs · workspace · sandbox port 3000",
  conversation: "c1",
  created_at: "2026-07-01T09:00:00Z",
  visibility: "workspace",
};

const NOTES = {
  name: "notes-def",
  summary: "notes · private · sandbox port 3001",
  conversation: "c2",
  created_at: "2026-07-02T09:00:00Z",
  visibility: "private",
};

function open() {
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="sites" />
    </MainAgentProvider>,
  );
}

beforeEach(() => {
  useStreamFake();
});

test("a site is a card carrying its band, its visibility, and its summary", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, [DOCS]) });
  open();

  const card = (await screen.findAllByRole("listitem"))[0];
  expect(card.querySelector('[data-part="primary"]')?.textContent).toBe("docs-abc");
  expect(card.querySelector('[data-part="status"]')?.textContent).toBe("Workspace");
  expect(card.querySelector('[data-part="body"]')?.textContent).toBe(DOCS.summary);
  const band = card.querySelector('[data-part="mark"]');
  expect(band?.className).toContain("h-(--size-band)");
  expect(band?.getAttribute("aria-hidden")).toBe("true");
});

test("the visibility tabs narrow the cards to one class", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, [DOCS, NOTES]) });
  open();

  expect(await screen.findByText("docs-abc")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Private" }));

  expect(screen.getByRole("tab", { name: "Private" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByText("notes-def")).toBeTruthy();
  expect(screen.queryByText("docs-abc")).toBeNull();
});

test("a search asks the kind for the match rather than filtering the page", async () => {
  const { calls } = wire({
    "/objects/site": (url) =>
      objectIndex(SITE_KIND, url.includes("q=notes") ? [NOTES] : [DOCS, NOTES]),
  });
  open();

  await screen.findByText("docs-abc");
  await userEvent.type(screen.getByPlaceholderText("Search"), "notes{Enter}");

  await waitFor(() => expect(calls.some((url) => url.includes("q=notes"))).toBe(true));
  expect(calls[0]).toContain("agent=" + AGENT_ID);
  expect(await screen.findByText("notes-def")).toBeTruthy();
  expect(screen.queryByText("docs-abc")).toBeNull();
});
