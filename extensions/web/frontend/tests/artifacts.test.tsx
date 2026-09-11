import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";
import type { Placement } from "@/kernel/pager";
import { Artifacts } from "@/views/Artifacts";

import { AGENT, CONVO_ID, MEMBER, NO_ARTIFACTS, SITE_KIND, json, objectIndex, wire } from "./harness";

const LOGO_SHEET = "ufo-logo-ratio.pdf";

function sharedFile(filename: string, mediaType: string) {
  return {
    name: "conv1-" + filename,
    filename,
    subject: null,
    media_type: mediaType,
    size_bytes: 64,
    shared_at: "2026-08-14T09:00:00Z",
    url: "/dl/" + filename,
    preview_url: null,
    owner_email: MEMBER.email,
    origin: null,
    conversation: CONVO_ID,
    surface: "web",
    source: null,
  };
}

function shelf(routes: Record<string, unknown>, place: Placement = {}) {
  wire(routes as Parameters<typeof wire>[0]);
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <Artifacts place={place} onPlace={() => {}} />
    </MainAgentProvider>,
  );
}

function waiting(place: Placement = {}) {
  return shelf(
    {
      "/objects/site": () => new Promise<Response>(() => {}),
      "/objects/artifact": () => new Promise<Response>(() => {}),
    },
    place,
  );
}

test("the shelf waits as the tiles it will draw, under its own toolbar", async () => {
  const { container } = waiting();

  expect(await screen.findByRole("tablist", { name: "Scope" })).toBeTruthy();
  const track = container.querySelector("ul")!;
  expect(track.className).toContain("minmax(var(--size-tile),1fr)");
  const tiles = track.querySelectorAll("li");
  expect(tiles.length).toBeGreaterThan(0);
  for (const tile of tiles) {
    expect(tile.className).toContain("flex flex-col gap-sm");
    const marks = tile.querySelectorAll('[data-part="skeleton"]');
    expect(marks.length).toBe(3);
    expect(marks[0].className).toContain("aspect-square w-full rounded-panel border border-edge");
  }
});

test("the list view waits on its own tracks, under the columns it will fill", async () => {
  const { container } = waiting({ face: "table" });

  const table = await screen.findByRole("table");
  expect(table.getAttribute("data-measured")).toBe("");
  expect(table.style.getPropertyValue("--table-floor")).toContain("--size-prose-column");
  for (const column of ["Name", "Details", "Type"]) {
    expect(screen.getByRole("columnheader", { name: column })).toBeTruthy();
  }
  const rows = table.querySelectorAll("tbody tr");
  expect(rows.length).toBeGreaterThan(0);
  for (const row of rows) {
    expect(row.querySelectorAll('[data-part="skeleton"]').length).toBe(5);
    expect(row.querySelector('[data-part="skeleton"]')?.className).toContain("size-(--size-lede)");
  }
  expect(container.querySelector("ul")).toBeNull();
});

test("the empty shelf stands the shipped logo sheet beside its note", async () => {
  shelf({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/objects/artifact": () => json({ objects: [] }),
  });

  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();
  expect(await screen.findByText(LOGO_SHEET)).toBeTruthy();
});

test("the list view stays a table rather than stacking into records", async () => {
  const filename = "q3-revenue-review-of-every-region.md";
  shelf(
    {
      "/objects/site": () => objectIndex(SITE_KIND, []),
      "/objects/artifact": () =>
        json({
          objects: [
            {
              ...sharedFile(filename, "text/markdown"),
              name: "Report",
              size_bytes: 12,
              shared_at: "2026-08-20T09:00:00+00:00",
              url: "https://example.test/report.md",
            },
          ],
        }),
    },
    { face: "table" },
  );

  const table = await screen.findByRole("table");
  expect(table.getAttribute("data-stacks")).toBeNull();
  expect(table.style.getPropertyValue("--table-floor")).toContain("--size-prose-column");
  expect(table.closest('[data-slot="table-container"]')?.className).toContain("overflow-x-auto");
  expect(table.getAttribute("data-measured")).toBe("");
  const name = await screen.findByText(filename);
  expect(name.className).not.toContain("truncate");
  expect(name.closest("td")?.className).not.toContain("truncate");
});

test("a cached picture becomes visible without a load event", async () => {
  Object.defineProperty(HTMLImageElement.prototype, "complete", {
    value: true,
    configurable: true,
  });
  Object.defineProperty(HTMLImageElement.prototype, "naturalWidth", {
    value: 320,
    configurable: true,
  });
  try {
    shelf(
      {
        "/objects/site": () => objectIndex(SITE_KIND, []),
        "/objects/artifact": () =>
          json({
            objects: [{ ...sharedFile("chart.png", "image/png"), preview_url: "/preview/chart.png" }],
          }),
      },
      { face: "table" },
    );

    await screen.findByText("chart.png");
    await vi.waitFor(() =>
      expect(document.querySelector('img[src="/preview/chart.png"]')?.className).toContain(
        "opacity-100",
      ),
    );
  } finally {
    Reflect.deleteProperty(HTMLImageElement.prototype, "complete");
    Reflect.deleteProperty(HTMLImageElement.prototype, "naturalWidth");
  }
});

test("the shelf draws code and plain text as their characters and labels the rest with their extension", async () => {
  shelf({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/objects/artifact": () =>
      json({
        objects: [
          sharedFile("deploy.py", "text/x-python"),
          sharedFile("compose.yaml", "application/yaml"),
          sharedFile("app.ts", "application/typescript"),
          sharedFile("bundle.zip", "application/zip"),
          sharedFile("archive.tar.gz", "application/octet-stream"),
        ],
      }),
    "/dl/deploy.py": () => new Response("print('deploying')"),
    "/dl/compose.yaml": () => new Response("services:\n  web: {}"),
    "/dl/app.ts": () => new Response("export const port = 8080;"),
    "/dl/bundle.zip": () => new Response("PK"),
    "/dl/archive.tar.gz": () => new Response("\u001f\u008b"),
  });

  expect(await screen.findByText("print('deploying')")).toBeTruthy();
  expect(await screen.findByText(/services:/)).toBeTruthy();
  expect(await screen.findByText("export const port = 8080;")).toBeTruthy();
  expect(screen.queryByText("PK")).toBeNull();
  expect(await screen.findByText("ZIP")).toBeTruthy();
  expect(await screen.findByText("TAR.GZ")).toBeTruthy();
});
