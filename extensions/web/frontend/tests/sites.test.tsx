import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, PlacedSection, SITE_KIND, objectIndex, useStreamFake, wire } from "./harness";

const DOCS_URL = "https://ufo.example/surface/sites/signed-docs";

const DOCS = {
  name: "docs-abc",
  summary: "docs · workspace · sandbox port 3000",
  conversation: "c1",
  created_at: "2026-07-01T09:00:00Z",
  visibility: "workspace",
  site_url: DOCS_URL,
  owner_email: "mel@example.com",
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
      <PlacedSection section="sites" />
    </MainAgentProvider>,
  );
}

beforeEach(() => {
  useStreamFake();
});

test("a site is a card carrying its band, its visibility, its summary, and its creator", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, [DOCS, NOTES]) });
  open();

  const card = (await screen.findAllByRole("listitem"))[0];
  expect(card.querySelector('[data-part="primary"]')?.textContent).toBe("docs-abc");
  expect(card.querySelector('[data-part="status"]')?.textContent).toBe("Workspace");
  expect(card.querySelector('[data-part="body"]')?.textContent).toBe(DOCS.summary);
  expect(card.querySelector('[data-part="meta"]')?.textContent).toBe("mel@example.com");
  const notes = screen.getByText("notes-def").closest("li");
  expect(notes?.querySelector('[data-part="body"]')?.textContent).toBe(NOTES.summary);
  expect(notes?.querySelector('[data-part="meta"]')?.textContent).toBe("Workspace");
  const band = card.querySelector('[data-part="mark"]');
  expect(band?.className).toContain("h-(--size-band)");
  expect(band?.getAttribute("aria-hidden")).toBe("true");
});

test("a site with a link opens it in a new tab, and one without draws no Open", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, [DOCS, NOTES]) });
  open();

  const docs = (await screen.findByText("docs-abc")).closest("li");
  const link = within(docs!).getByRole("link", { name: "Open" });
  expect(link.getAttribute("href")).toBe(DOCS_URL);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(within(docs!).getByRole("button", { name: "View" })).toBeTruthy();

  const notes = screen.getByText("notes-def").closest("li");
  expect(within(notes!).queryByRole("link", { name: "Open" })).toBeNull();
  expect(within(notes!).getByRole("button", { name: "View" })).toBeTruthy();
});

test("Mine asks the server for sites created by the member", async () => {
  const { calls } = wire({
    "/objects/site": (url) =>
      objectIndex(SITE_KIND, url.includes("mine=true") ? [NOTES] : [DOCS, NOTES]),
  });
  open();

  expect(await screen.findByText("docs-abc")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Mine" }));

  expect(screen.getByRole("tab", { name: "Mine" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.getByText("notes-def")).toBeTruthy();
  await waitFor(() => expect(calls.some((url) => url.includes("mine=true"))).toBe(true));
});

test("empty sites names the selected scope", async () => {
  wire({ "/objects/site": () => objectIndex(SITE_KIND, []) });
  open();

  expect(await screen.findByText("No sites yet.")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Mine" }));
  expect(await screen.findByText("You have not created a site yet.")).toBeTruthy();
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
