import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { Viewer } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";
import { parseHash, sectionHash, type PlaceStep, type WorkspacePlace } from "@/lib/route";
import { TabbedPane } from "@/views/TabbedPane";
import { SECTION_VIEWS } from "@/views/registry";

import {
  AGENT,
  AGENT_ID,
  NO_ARTIFACTS,
  PlacedSection,
  SITE_KIND,
  TASK_KIND,
  TRIGGER_KIND,
  json,
  objectIndex,
  useStreamFake,
  viewCard,
  wire,
} from "./harness";

const TEXT_URL = "/dl/notes.txt";
const DOCS_URL = "https://ufo.example/surface/sites/signed-docs";
const DOCS_PREVIEW_URL = "/artifacts/1f0d/docs.png?preview=image%2Fpng%3A5148";

/** A site's lane: the app whose namespace holds it, the kind and the name, in the one string the
 *  address and the store both carry. */
const DOCS_LANE = "object/" + AGENT_ID + "/site/docs-abc";
const NOTES_LANE = "object/" + AGENT_ID + "/site/notes-def";

const DOCS = {
  name: "docs-abc",
  summary: "docs · workspace · sandbox port 3000",
  conversation: "c1",
  created_at: "2026-07-01T09:00:00Z",
  visibility: "workspace",
  site_url: DOCS_URL,
};

const NOTES = {
  name: "notes-def",
  summary: "notes · private · sandbox port 3001",
  conversation: "c2",
  created_at: "2026-07-02T09:00:00Z",
  visibility: "private",
};

const artifact = (over: Record<string, unknown> = {}) => ({
  id: "a1",
  filename: "notes.txt",
  subject: "notes",
  media_type: "text/plain",
  size_bytes: 12,
  created_at: "2026-07-31T09:00:00",
  url: TEXT_URL,
  preview_url: null,
  owner_email: "member@example.com",
  origin: null,
  conversation_id: "c1",
  surface: "web",
  source: null,
  ...over,
});

function streamOf(chunks: Uint8Array[], offered: { count: number }) {
  let at = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (at >= chunks.length) {
        controller.close();
        return;
      }
      offered.count += 1;
      controller.enqueue(chunks[at]);
      at += 1;
    },
  });
}

/** The shelf's narrowings live behind the filter glyph, so a test picking one opens the menu the
 *  member opens rather than reaching past it. */
async function narrowTo(family: string) {
  await userEvent.click(await screen.findByRole("button", { name: "Filter" }));
  await userEvent.click(await screen.findByRole("menuitemradio", { name: family }));
}

async function facets(): Promise<string[]> {
  await userEvent.click(await screen.findByRole("button", { name: "Filter" }));
  return screen.getAllByRole("menuitemradio").map((item) => item.textContent ?? "");
}

function only(artifacts: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("/objects/site") ? objectIndex(SITE_KIND, []) : json({ artifacts }),
    ),
  );
}

beforeEach(() => {
  useStreamFake();
  localStorage.clear();
});

test("the artifact listing sends search and media filters to its read", async () => {
  const { calls } = wire({
    "/workspace/artifacts": () => json({ artifacts: [artifact()] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  expect(await facets()).toEqual(["All", "Sites", "Images", "Documents", "Other"]);
  await userEvent.click(screen.getByRole("menuitemradio", { name: "Images" }));
  await waitFor(() => expect(calls.some((url) => url.includes("media=image"))).toBe(true));

  await userEvent.type(screen.getByRole("searchbox"), "report{Enter}");
  await waitFor(() => expect(calls.some((url) => url.includes("q=report"))).toBe(true));
});

test("narrowing a paged listing reads from the start, not from the page it was on", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [artifact()], older: "page-2" }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "Older" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after=page-2"))).toBe(true));

  await narrowTo("Images");
  await waitFor(() => expect(calls.some((url) => url.includes("media=image"))).toBe(true));
  expect(calls.filter((url) => url.includes("media=image")).some((url) => url.includes("after="))).toBe(false);

  await userEvent.type(screen.getByRole("searchbox"), "report{Enter}");
  await waitFor(() => expect(calls.some((url) => url.includes("q=report"))).toBe(true));
  expect(calls.filter((url) => url.includes("q=report")).some((url) => url.includes("after="))).toBe(false);
});

test("a text artifact opens in the viewer, reads its body, and closes back to the listing", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      return new Response("hello from the file");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("hello from the file")).toBeTruthy();
  const slot = screen.getByRole("region", { name: "notes.txt" });
  expect(within(slot).getByText(/^notes · member@example.com/).textContent).toBe(
    "notes · member@example.com · text/plain · 12 B · Jul 31 2026",
  );

  await userEvent.click(screen.getByRole("button", { name: "Close notes.txt" }));
  await waitFor(() => expect(screen.queryByText("hello from the file")).toBeNull());
  expect(await viewCard("notes.txt")).toBeTruthy();
});

test("the listing stays reachable behind an open viewer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      return new Response("hello from the file");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("hello from the file")).toBeTruthy();

  expect(await viewCard("notes.txt")).toBeTruthy();
});

test("leaving the section takes the viewer with it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      if (url.includes("/workspace/radar")) return json({ runs: [] });
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/objects/scheduled_task")) return objectIndex(TASK_KIND, []);
      if (url.includes("/objects/source_trigger")) return objectIndex(TRIGGER_KIND, []);
      if (url.includes("/workspace/team"))
        return json({ members: [], can_add: false, domain: null });
      return new Response("hello from the file");
    }),
  );
  const view = render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("hello from the file")).toBeTruthy();

  view.rerender(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="radar" />
    </MainAgentProvider>,
  );

  await waitFor(() => expect(screen.queryByText("hello from the file")).toBeNull());
  expect(screen.queryByRole("button", { name: /^Close/ })).toBeNull();
});

test("the viewer bounds a long read by bytes, not characters, and cancels the rest", async () => {
  const offered = { count: 0 };
  const twoByte = new TextEncoder().encode("é".repeat(512));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) {
        return json({ artifacts: [artifact({ size_bytes: 200 * 1024 })] });
      }
      return new Response(streamOf(Array.from({ length: 200 }, () => twoByte), offered));
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("First 64 kB shown.")).toBeTruthy();
  const shown = document.querySelector("pre")!.textContent ?? "";
  expect(new TextEncoder().encode(shown).length).toBeLessThanOrEqual(64 * 1024);
  expect(offered.count).toBeLessThanOrEqual(67);
});

test("a body of exactly the bound renders whole and claims nothing about truncation", async () => {
  const offered = { count: 0 };
  const exact = new Uint8Array(64 * 1024).fill(65);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) {
        return json({ artifacts: [artifact({ size_bytes: 64 * 1024 })] });
      }
      return new Response(streamOf([exact], offered));
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  await waitFor(() => expect(document.querySelector("pre")).not.toBeNull());
  expect(document.querySelector("pre")!.textContent!.length).toBe(64 * 1024);
  expect(screen.queryByText("First 64 kB shown.")).toBeNull();
});

test("a read that fails mid-body states the fault instead of staying on Loading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      return new Response(
        new ReadableStream<Uint8Array>({
          pull() {
            throw new Error("the body died");
          },
        }),
      );
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
  expect(screen.queryByText("Loading…")).toBeNull();
});

test("a read whose request never lands states the fault instead of staying on Loading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      throw new Error("the request died");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
  expect(screen.queryByText("Loading…")).toBeNull();
});

test("an image whose link expired states it rather than showing an empty panel", async () => {
  only([artifact({ media_type: "image/png", filename: "shot.png" })]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("shot.png"));
  const slot = screen.getByRole("region", { name: "shot.png" });
  const full = slot.querySelector("img") as HTMLImageElement;
  expect(full).not.toBeNull();
  full.dispatchEvent(new Event("error"));

  expect(
    await screen.findByText(
      "The image did not load. Its link may have expired — reload the listing.",
    ),
  ).toBeTruthy();
});

test("a type with no preview says to download it, and the viewer offers that download", async () => {
  only([artifact({ media_type: "application/zip", filename: "bundle.zip" })]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("bundle.zip"));
  expect(
    await screen.findByText("No preview for this file type. Download it to open it."),
  ).toBeTruthy();
  const download = screen.getByRole("link", { name: "Download" });
  expect(download.getAttribute("download")).toBe("bundle.zip");
});

test("the viewer leads back to the conversation, and out to the Slack thread it came in on", async () => {
  only([
    artifact({
      media_type: "application/zip",
      filename: "bundle.zip",
      surface: "slack",
      source: "https://acme.slack.com/archives/C1/p1700000000000100",
    }),
  ]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("bundle.zip"));
  const conversation = await screen.findByRole("link", { name: "Conversation" });
  expect(conversation.getAttribute("href")).toBe("#/c/c1");
  const thread = screen.getByRole("link", { name: "Slack" });
  expect(thread.textContent).toBe("Slack ↗");
  expect(thread.getAttribute("href")).toBe(
    "https://acme.slack.com/archives/C1/p1700000000000100",
  );
  expect(thread.getAttribute("target")).toBe("_blank");
  expect(thread.getAttribute("rel")).toBe("noopener noreferrer");
});

test("a portal conversation's viewer draws no Slack way out", async () => {
  only([artifact({ media_type: "application/zip", filename: "bundle.zip" })]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("bundle.zip"));
  expect(await screen.findByRole("link", { name: "Conversation" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Slack" })).toBeNull();
});

test("an artifact with no link stays plain text and opens nothing", async () => {
  only([artifact({ url: null })]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("notes.txt")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "View" })).toBeNull();
});

test("Escape dismisses the viewer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      return new Response("body");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("body")).toBeTruthy();

  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("body")).toBeNull());
});

test("closing the viewer discards a body still in flight", async () => {
  let releaseFirst: ((value: Response) => void) | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) {
        return json({
          artifacts: [artifact(), artifact({ id: "a2", filename: "second.txt", url: "/dl/second.txt" })],
        });
      }
      if (url === TEXT_URL) {
        return new Promise<Response>((resolve) => {
          releaseFirst = resolve;
        });
      }
      return new Response("the second body");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await viewCard("notes.txt"));
  await waitFor(() => expect(releaseFirst).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Close notes.txt" }));

  releaseFirst!(new Response("the first body"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("the first body")).toBeNull();
  expect(await viewCard("notes.txt")).toBeTruthy();
});

test("the artifacts listing renders as tiles, each led by its own picture", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      return new Response("x");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("notes.txt"),
  );
  expect(item?.querySelector('[data-part="primary"]')?.textContent).toBe("notes.txt");
  expect(item?.querySelector('[data-part="status"]')?.textContent).toBe("Jul 31 2026");
  /* A tile states its picture, its name and its moment. The sentence and the owner line a card
     carried are read in the table shape and in the viewer, not under a picture. */
  expect(item?.querySelector('[data-part="body"]')).toBeNull();
  expect(item?.querySelector('[data-part="meta"]')).toBeNull();
  const picture = item?.querySelector('[data-part="mark"]');
  expect(picture?.className).toContain("aspect-square");
  expect(picture?.querySelector("img")).toBeNull();
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

test("an image artifact fills its band from the top, and a dead link leaves the placeholder", async () => {
  only([artifact({ media_type: "image/png", filename: "shot.png" })]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("shot.png"),
  );
  const band = item?.querySelector('[data-part="mark"]') as HTMLElement;
  const image = band.querySelector("img") as HTMLImageElement;
  expect(image.getAttribute("src")).toBe(TEXT_URL);
  expect(image.getAttribute("alt")).toBe("");
  expect(image.className).toContain("object-cover");
  expect(image.className).toContain("object-top");

  image.dispatchEvent(new Event("error"));
  await waitFor(() => expect(band.querySelector("img")).toBeNull());
});

test("a markdown artifact's band reads its first lines", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts"))
        return json({
          artifacts: [
            artifact({ media_type: "text/markdown", filename: "notes.md", url: "/dl/notes.md" }),
          ],
        });
      return new Response("# Verdict\n\nblocked, 2 defects");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("notes.md"),
  );
  const band = item?.querySelector('[data-part="mark"]') as HTMLElement;
  await waitFor(() => expect(band.textContent).toContain("Verdict"));
  expect(band.querySelector("[data-artifact-document]")).not.toBeNull();
  expect(band.querySelector("img")).toBeNull();
});

test("json and patch bands read their characters as fingerprints", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts"))
        return json({
          artifacts: [
            artifact({
              media_type: "application/json",
              filename: "runs.json",
              url: "/dl/runs.json",
            }),
            artifact({
              id: "a2",
              media_type: "text/x-patch",
              filename: "fix.patch",
              url: "/dl/fix.patch",
              created_at: "2026-07-30T09:00:00",
            }),
          ],
        });
      if (url.includes("runs.json")) return new Response('{"runs": 12}');
      return new Response("--- a/loop.py\n+++ b/loop.py");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const items = await screen.findAllByRole("listitem");
  const jsonBand = items
    .find((entry) => entry.textContent?.includes("runs.json"))
    ?.querySelector('[data-part="mark"]') as HTMLElement;
  const patchBand = items
    .find((entry) => entry.textContent?.includes("fix.patch"))
    ?.querySelector('[data-part="mark"]') as HTMLElement;
  await waitFor(() => expect(jsonBand.textContent).toContain('{"runs": 12}'));
  await waitFor(() => expect(patchBand.textContent).toContain("+++ b/loop.py"));
  expect(jsonBand.querySelector("pre")).not.toBeNull();
});

test("a csv artifact draws as its table, in the band and in the viewer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts"))
        return json({
          artifacts: [
            artifact({ media_type: "text/csv", filename: "spend.csv", url: "/dl/spend.csv" }),
          ],
        });
      return new Response('member,spend\n"Ng, Ada",12\nBo,"3 ""units"""');
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("spend.csv"),
  );
  const band = item?.querySelector('[data-part="mark"]') as HTMLElement;
  await waitFor(() => expect(band.querySelector("table")).not.toBeNull());
  expect(band.querySelectorAll("th")[1]?.textContent).toBe("spend");
  const cells = [...band.querySelectorAll("td")].map((cell) => cell.textContent);
  expect(cells).toEqual(["Ng, Ada", "12", "Bo", '3 "units"']);

  await userEvent.click(await viewCard("spend.csv"));
  const slot = screen.getByRole("region", { name: "spend.csv" });
  await waitFor(() => expect(within(slot).getAllByRole("table").length).toBeGreaterThan(0));
});

test("a plain text artifact's band stays the placeholder", async () => {
  only([artifact()]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("notes.txt"),
  );
  const band = item?.querySelector('[data-part="mark"]') as HTMLElement;
  expect(band.querySelector("img")).toBeNull();
  expect(band.textContent).toBe("");
});

test("a document's rendered page fills its band and its viewer", async () => {
  only([
    artifact({
      media_type: "application/pdf",
      filename: "report.pdf",
      preview_url: "/dl/report-preview.png",
    }),
  ]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("report.pdf"),
  );
  const band = item?.querySelector('[data-part="mark"]') as HTMLElement;
  expect(band.querySelector("img")?.getAttribute("src")).toBe("/dl/report-preview.png");

  await userEvent.click(await viewCard("report.pdf"));
  const slot = screen.getByRole("region", { name: "report.pdf" });
  expect(slot.querySelector("img[src='/dl/report-preview.png']")).not.toBeNull();
  expect(
    screen.queryByText("No preview for this file type. Download it to open it."),
  ).toBeNull();
});

test("an image's band prefers its validated preview link over the download link", async () => {
  only([
    artifact({
      media_type: "image/png",
      filename: "shot.png",
      preview_url: "/dl/shot-preview.png",
    }),
  ]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("shot.png"),
  );
  const band = item?.querySelector('[data-part="mark"]') as HTMLElement;
  expect(band.querySelector("img")?.getAttribute("src")).toBe("/dl/shot-preview.png");
});

function shelf(objects: unknown[], artifacts: unknown[]) {
  const term = (url: string) => new URLSearchParams(url.split("?")[1] ?? "").get("q") ?? "";
  return wire({
    "/objects/site": (url) =>
      objectIndex(
        SITE_KIND,
        objects.filter((row) => (row as { name: string }).name.includes(term(url))),
      ),
    "/workspace/artifacts": (url) =>
      json({
        artifacts: artifacts.filter((entry) =>
          (entry as { filename: string }).filename.includes(term(url)),
        ),
      }),
  });
}

function open() {
  return render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" />
    </MainAgentProvider>,
  );
}

const SECOND_URL = "/dl/second.txt";
const NOTES_KEY = "a1";
const SECOND_KEY = "a2";
const TRACK_SEPARATOR = "%7E";

const ODD_URL = "/dl/odd.txt";
const ODD_ID = "a9";
const ODD_NAME = "~$budget~v2-" + "and-the-whole-quarter-".repeat(20) + "final.txt";

function oddFile() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts"))
        return json({ artifacts: [artifact({ id: ODD_ID, filename: ODD_NAME, url: ODD_URL })] });
      return new Response("the odd body");
    }),
  );
}

function twoFiles() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
      if (url.includes("/workspace/artifacts"))
        return json({
          artifacts: [
            artifact(),
            artifact({
              id: "a2",
              filename: "second.txt",
              url: SECOND_URL,
              created_at: "2026-07-30T09:00:00",
            }),
          ],
        });
      return new Response(url === SECOND_URL ? "the second body" : "the first body");
    }),
  );
  return { calls };
}

const siteDetail = (name: string, links: unknown[]) => ({
  ...SITE_KIND,
  name,
  summary: name + " · workspace",
  spec: { visibility: "workspace" },
  status: { visibility: "workspace", owner_email: "member@example.com" },
  links,
  created_at: "2026-07-01T09:00:00Z",
  updated_at: null,
});

/** Two sites, the first of which names the second among its links — the one act on this screen
 *  that opens a lane from inside another lane. */
function linkedSites() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/objects/site/docs-abc"))
        return json(
          siteDetail("docs-abc", [
            { relation: "reports_to", kind: "site", name: "notes-def", opens: true },
          ]),
        );
      if (url.includes("/objects/site/notes-def")) return json(siteDetail("notes-def", []));
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, [DOCS, NOTES]);
      return json({ artifacts: [] });
    }),
  );
}

const standing = () =>
  screen.getAllByRole("region").map((slot) => slot.getAttribute("aria-label"));

/** The screen with its own place recorder read out: the address the track would be carried on, and
 *  the history step each press earned. A press that changes nothing has to be seen not to push, and
 *  a place held in a test's own state says nothing about that. */
function tracked() {
  const steps: PlaceStep[] = [];
  const places: WorkspacePlace[] = [];
  function Screen() {
    const [place, setPlace] = useState<WorkspacePlace>({});
    return (
      <TabbedPane
        group="section"
        tabs={["artifacts"] as const}
        views={SECTION_VIEWS}
        view="artifacts"
        place={place}
        onPlace={(_view, next, step) => {
          steps.push(step);
          places.push(next);
          setPlace(next);
        }}
      />
    );
  }
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Screen />
    </MainAgentProvider>,
  );
  return {
    steps,
    place: () => places.at(-1) ?? {},
    address: () => sectionHash("artifacts", places.at(-1) ?? {}),
  };
}

/** The band heads the pane rather than the words under it: above the scroller, at the pane's own
 *  width, so the name sits at the pane's left edge and the rule under it reaches both edges. The
 *  reading measure and the gutter are the content's alone. */
test("the shelf is headed by a band at the pane's own width", async () => {
  twoFiles();
  open();

  const named = await screen.findByRole("heading", { level: 1, name: "Artifacts" });
  const band = named.closest("[data-slot=header]") as HTMLElement;
  expect(band.className).not.toContain("border-b");
  expect(band.closest(".overflow-y-auto")).toBeNull();
  expect(band.closest(".max-w-page")).toBeNull();
});

/** A lane says the surface it was opened out of, which is the way back a member reading one lane
 *  to a screen has instead of the shelf standing beside it. */
test("a file's lane says the shelf it was opened from, and the crumb shuts it", async () => {
  twoFiles();
  open();

  await userEvent.click(await viewCard("notes.txt"));

  const lane = await screen.findByRole("region", { name: "notes.txt" });
  const path = within(lane).getByRole("navigation", { name: "Breadcrumb" });
  expect(path.textContent).toBe("Artifacts/notes.txt");

  await userEvent.click(within(path).getByRole("button", { name: "Back to Artifacts" }));

  await waitFor(() => expect(screen.queryAllByRole("region")).toEqual([]));
});

/** The track is a path, not a workbench: a press in the shelf shuts every lane the shelf opened
 *  and stands the record it names in the one lane left. */
test("a second file pressed on the shelf takes the first one's place", async () => {
  twoFiles();
  const { address } = tracked();

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("the first body")).toBeTruthy();

  await userEvent.click(await viewCard("second.txt"));
  expect(await screen.findByText("the second body")).toBeTruthy();

  await waitFor(() => expect(screen.queryByText("the first body")).toBeNull());
  expect(standing()).toEqual(["second.txt"]);
  expect(address()).toBe("#/artifacts?open=" + encodeURIComponent(SECOND_KEY));
});

/** A filename is whatever the agent sharing the file called it: the character a track is written
 *  with is an ordinary one in a name — the `~$budget.xlsx` a spreadsheet leaves beside the file it
 *  has open — and nothing bounds how long it runs. A lane is named by the file's own id rather than
 *  by anything read off the name, so such a file opens and its address carries it like any other. */
test("a file named oddly and past what a lane id holds opens, and the address carries it", async () => {
  oddFile();
  const { address, place } = tracked();

  await userEvent.click(await viewCard(ODD_NAME));

  expect(await screen.findByText("the odd body")).toBeTruthy();
  expect(standing()).toEqual([ODD_NAME]);
  expect(address()).toBe("#/artifacts?open=" + ODD_ID);
  expect(parseHash(address())).toEqual({ kind: "section", section: "artifacts", place: place() });
});

test("a card pressed with the modifier down opens beside, and the address carries both", async () => {
  twoFiles();
  const { address } = tracked();
  const user = userEvent.setup();

  await user.click(await viewCard("notes.txt"));
  expect(await screen.findByText("the first body")).toBeTruthy();

  await user.keyboard("{Meta>}");
  await user.click(await viewCard("second.txt"));
  await user.keyboard("{/Meta}");

  expect(await screen.findByText("the second body")).toBeTruthy();
  expect(screen.getByText("the first body")).toBeTruthy();
  expect(standing()).toEqual(["notes.txt", "second.txt"]);
  expect(address()).toBe(
    "#/artifacts?open=" +
      encodeURIComponent(NOTES_KEY) +
      TRACK_SEPARATOR +
      encodeURIComponent(SECOND_KEY),
  );
});

test("a middle press opens beside", async () => {
  twoFiles();
  const user = userEvent.setup();
  open();

  await user.click(await viewCard("notes.txt"));
  expect(await screen.findByText("the first body")).toBeTruthy();

  await user.pointer({ keys: "[MouseMiddle]", target: await viewCard("second.txt") });

  expect(await screen.findByText("the second body")).toBeTruthy();
  expect(standing()).toEqual(["notes.txt", "second.txt"]);
});

/** A middle press on a site's own address is that link's, and stays the browser's: the shelf opens
 *  nothing behind the tab it lands in. */
test("a middle press on a site's own address opens no slot", async () => {
  shelf([DOCS], []);
  const user = userEvent.setup();
  open();

  await user.pointer({
    keys: "[MouseMiddle]",
    target: await screen.findByRole("link", { name: "Open" }),
  });

  expect(screen.queryAllByRole("region")).toEqual([]);
});

test("pressing the card already standing changes nothing", async () => {
  const { calls } = twoFiles();
  const { steps } = tracked();

  await userEvent.click(await viewCard("notes.txt"));
  expect(await screen.findByText("the first body")).toBeTruthy();
  await userEvent.click(await viewCard("notes.txt"));

  expect(standing()).toEqual(["notes.txt"]);
  expect(screen.getByText("the first body")).toBeTruthy();
  /* The second press is no history entry of its own, and the body already read is not read
     again — the slot holding it never moved. */
  expect(steps).toEqual(["push", "replace"]);
  expect(calls.filter((url) => url === TEXT_URL)).toHaveLength(1);
});

test("closing a lane shuts what was opened from it", async () => {
  linkedSites();
  open();

  await userEvent.click(await viewCard("docs-abc"));
  const record = await screen.findByRole("region", { name: "docs-abc" });
  await userEvent.click(await within(record).findByRole("button", { name: /notes-def/ }));

  await waitFor(() => expect(standing()).toEqual(["docs-abc", "notes-def"]));

  await userEvent.click(screen.getByRole("button", { name: "Close docs-abc" }));

  await waitFor(() => expect(screen.queryAllByRole("region")).toEqual([]));
});

/** The mark is the row's whole band and not the letters of its name, so it is read off the element
 *  the grid and the table make a record out of: a tile's `li`, a table's `tr`. `bg-fill` is the
 *  band the portal already lights a picked row with; `hover:bg-fill` is a different rule and is not
 *  the mark, so the class is matched whole. */
const banded = (band: HTMLElement) => ({
  name: band.querySelector("[data-part=primary], td")?.textContent,
  marked: band.getAttribute("aria-current"),
  lit: /(?:^|\s)bg-fill(?:\s|$)/.test(band.className),
});

/** The shelf's own bands, read inside the page rather than off the whole screen: a lane standing
 *  beside it is headed by the path it was opened along, and a crumb is a list of items too. */
const tiles = () => within(screen.getByTestId("section")).getAllByRole("listitem").map(banded);
const lines = () => screen.getAllByRole("row").slice(1).map(banded);

/** Finder keeps the selected row lit in every column, which is what makes a press that shuts the
 *  lanes to its right read as navigation rather than as slots leaving. */
test("the shelf marks the whole band of the record whose document is standing", async () => {
  twoFiles();
  open();

  await screen.findByText("notes.txt");
  expect(tiles()).toEqual([
    { name: "notes.txt", marked: null, lit: false },
    { name: "second.txt", marked: null, lit: false },
  ]);

  await userEvent.click(await viewCard("notes.txt"));
  await waitFor(() =>
    expect(tiles()).toEqual([
      { name: "notes.txt", marked: "true", lit: true },
      { name: "second.txt", marked: null, lit: false },
    ]),
  );

  await userEvent.click(await viewCard("second.txt"));
  await waitFor(() =>
    expect(tiles()).toEqual([
      { name: "notes.txt", marked: null, lit: false },
      { name: "second.txt", marked: "true", lit: true },
    ]),
  );

  await userEvent.click(screen.getByRole("button", { name: "Close second.txt" }));
  await waitFor(() => expect(screen.queryAllByRole("region")).toEqual([]));
  expect(tiles()).toEqual([
    { name: "notes.txt", marked: null, lit: false },
    { name: "second.txt", marked: null, lit: false },
  ]);
});

/** The two faces mark one way. A row a table lights is still the control that opens the record —
 *  the mark rides beside `rowControl`, it does not stand in for it. */
test("the table face marks the standing record on its row, and the row still opens", async () => {
  twoFiles();
  open();

  await userEvent.click(await screen.findByRole("radio", { name: "Table" }));
  await waitFor(() =>
    expect(lines()).toEqual([
      { name: "notes.txt", marked: null, lit: false },
      { name: "second.txt", marked: null, lit: false },
    ]),
  );

  await userEvent.click(screen.getAllByRole("row")[2]);
  expect(await screen.findByText("the second body")).toBeTruthy();
  expect(lines()).toEqual([
    { name: "notes.txt", marked: null, lit: false },
    { name: "second.txt", marked: "true", lit: true },
  ]);

  await userEvent.click(screen.getAllByRole("row")[1]);
  expect(await screen.findByText("the first body")).toBeTruthy();
  expect(standing()).toEqual(["notes.txt"]);
  expect(lines()).toEqual([
    { name: "notes.txt", marked: "true", lit: true },
    { name: "second.txt", marked: null, lit: false },
  ]);
});

/** The conversation a file came in on is another screen, not a record of this one, so it is
 *  followed rather than opened beside the file. */
test("the viewer's conversation link leads to that screen instead of opening a slot", async () => {
  only([artifact({ media_type: "application/zip", filename: "bundle.zip" })]);
  open();

  await userEvent.click(await viewCard("bundle.zip"));
  const conversation = await screen.findByRole("link", { name: "Conversation" });
  expect(conversation.getAttribute("href")).toBe("#/c/c1");

  await userEvent.click(conversation);

  expect(location.hash).toBe("#/c/c1");
  expect(standing()).toEqual(["bundle.zip"]);
});

test("a file tile binds its parts to the artifact payload", async () => {
  shelf([], [artifact({ filename: "report.txt", subject: "member@example.com" })]);
  open();

  const card = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("report.txt"),
  );
  expect(card?.querySelector('[data-part="primary"]')?.textContent).toBe("report.txt");
  expect(card?.querySelector('[data-part="status"]')?.textContent).toBe("Jul 31 2026");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

test("a tile is one control, and the grid around it stays a list", async () => {
  shelf([], [artifact()]);
  open();

  const card = (await screen.findAllByRole("listitem"))[0];
  const tile = within(card).getByRole("button");
  expect(tile.tagName).toBe("BUTTON");
  expect(tile.textContent).toContain("notes.txt");
  /* `rowControl` would trade the row's own role for `button` and take the grid out of the list
     it is announced as. A tile carries no acts inside it, so it is a button outright. */
  expect(card.getAttribute("role")).toBeNull();
});

test("a site is a tile carrying its name and date, and states the rest in the table", async () => {
  shelf([DOCS], []);
  open();

  const card = (await screen.findAllByRole("listitem"))[0];
  expect(card.querySelector('[data-part="primary"]')?.textContent).toBe("docs-abc");
  expect(card.querySelector('[data-part="status"]')?.textContent).toBe("Jul 1 2026");
  const picture = card.querySelector('[data-part="mark"]');
  expect(picture?.className).toContain("aspect-square");
  expect(picture?.getAttribute("aria-hidden")).toBe("true");
  expect(picture?.querySelector("img")).toBeNull();

  await userEvent.click(screen.getByRole("radio", { name: "Table" }));
  const row = (await screen.findAllByRole("row"))[1];
  expect(within(row).getByText("docs-abc")).toBeTruthy();
  expect(within(row).getByText(DOCS.summary)).toBeTruthy();
  expect(within(row).getByText("Site")).toBeTruthy();
});

test("a site's captured page fills its band", async () => {
  shelf([{ ...DOCS, preview_url: DOCS_PREVIEW_URL }], []);
  open();

  const card = (await screen.findAllByRole("listitem"))[0];
  const band = card.querySelector('[data-part="mark"]') as HTMLElement;
  const image = band.querySelector("img") as HTMLImageElement;
  expect(image.getAttribute("src")).toBe(DOCS_PREVIEW_URL);
  expect(image.getAttribute("alt")).toBe("");
});

test("a site with a link opens it in a new tab, and one without draws no Open", async () => {
  shelf([DOCS, NOTES], []);
  open();

  const docs = (await screen.findByText("docs-abc")).closest("li");
  const link = within(docs!).getByRole("link", { name: "Open" });
  expect(link.getAttribute("href")).toBe(DOCS_URL);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(within(docs!).getByRole("button")).toBeTruthy();

  const notes = screen.getByText("notes-def").closest("li");
  expect(within(notes!).queryByRole("link", { name: /Open/ })).toBeNull();
  expect(within(notes!).getByRole("button")).toBeTruthy();
});

test("the shelf merges sites and files newest first, and reads sites newest first", async () => {
  const { calls } = shelf([DOCS], [artifact()]);
  open();

  await screen.findByText("docs-abc");
  const names = screen
    .getAllByRole("listitem")
    .map((card) => card.querySelector('[data-part="primary"]')?.textContent);
  expect(names).toEqual(["notes.txt", "docs-abc"]);
  expect(
    calls.some(
      (url) =>
        url.includes("/objects/site?agent=" + AGENT_ID) &&
        url.includes("order_by=created_at") &&
        url.includes("order=desc"),
    ),
  ).toBe(true);
});

const cursorAt = (stamp: string, side = "older") =>
  side + "|" + stamp + "|11111111-1111-4111-8111-111111111111";

test("sites stand on the shelf's newest page and no other", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, [DOCS]),
    "/workspace/artifacts": (url) =>
      url.includes("after=older")
        ? json({
            artifacts: [artifact({ filename: "older.txt", created_at: DOCS.created_at })],
            newer: cursorAt("2026-07-31T09:00:00", "newer"),
          })
        : json({ artifacts: [artifact()], older: cursorAt("2026-07-31T09:00:00") }),
  });
  open();

  expect(await screen.findByText("docs-abc")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  expect(await screen.findByText("older.txt")).toBeTruthy();
  expect(screen.queryByText("docs-abc")).toBeNull();

  /* Walking back up lands on the newest page again, and it carries a cursor saying so. The shelf
     judges by the payload rather than by that cursor, so the sites come back with it. */
  await userEvent.click(screen.getByRole("button", { name: "Newer" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after=newer"))).toBe(true));
  await screen.findByText("notes.txt");
  expect(screen.getByText("docs-abc")).toBeTruthy();
});

test("the filter menu narrows the shelf to sites or to one media kind", async () => {
  const { calls } = shelf([DOCS], [artifact()]);
  open();

  await screen.findByText("docs-abc");
  await narrowTo("Sites");
  expect(screen.queryByText("notes.txt")).toBeNull();

  await narrowTo("Documents");
  await waitFor(() => expect(calls.some((url) => url.includes("media=document"))).toBe(true));
  expect(await screen.findByText("notes.txt")).toBeTruthy();
  expect(screen.queryByText("docs-abc")).toBeNull();
});

test("the search reaches both families", async () => {
  const { calls } = shelf([DOCS], [artifact()]);
  open();

  await screen.findByText("docs-abc");
  await userEvent.type(screen.getByPlaceholderText("Search"), "docs{Enter}");

  await waitFor(() =>
    expect(calls.filter((url) => url.includes("q=docs")).length).toBe(2),
  );
  expect(await screen.findByText("docs-abc")).toBeTruthy();
  expect(screen.queryByText("notes.txt")).toBeNull();
});

test("a deploy with no sites extension lists its files and states no fault", async () => {
  wire({
    "/objects/site": () => new Response("no object kind named 'site'", { status: 404 }),
    "/workspace/artifacts": () => json({ artifacts: [artifact()] }),
  });
  open();

  expect(await screen.findByText("notes.txt")).toBeTruthy();
  expect(screen.queryByText(/^Error /)).toBeNull();
  /* A family that does not exist on this deploy is not offered, and the file types still are. */
  expect(await facets()).toEqual(["All", "Images", "Documents", "Other"]);
});

test("the shape switch redraws the same records as a table, and holds the choice", async () => {
  shelf([], [artifact()]);
  open();

  expect(await screen.findAllByRole("listitem")).toHaveLength(1);
  expect(screen.queryAllByRole("columnheader")).toEqual([]);

  await userEvent.click(screen.getByRole("radio", { name: "Table" }));
  expect(screen.queryAllByRole("listitem")).toEqual([]);
  expect(screen.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Name",
    "Details",
    "Type",
    "",
  ]);
  const row = screen.getAllByRole("row")[1];
  expect(within(row).getByText("notes.txt")).toBeTruthy();
  expect(within(row).getByText("plain")).toBeTruthy();

  await userEvent.click(screen.getByRole("radio", { name: "Tiles" }));
  expect(await screen.findAllByRole("listitem")).toHaveLength(1);
});

test("a file with no picture is marked by the glyph for its type", async () => {
  shelf([], [artifact({ filename: "sheet.csv", media_type: "text/csv" })]);
  open();

  await userEvent.click(await screen.findByRole("radio", { name: "Table" }));
  const row = screen.getAllByRole("row")[1];
  expect(within(row).getByText("csv")).toBeTruthy();
  expect(row.querySelector("svg")).not.toBeNull();
  expect(row.querySelector("img")).toBeNull();
});

test("an empty shelf states what lands in it", async () => {
  shelf([], []);
  open();

  expect(await screen.findByText(NO_ARTIFACTS)).toBeTruthy();
});

test("the scope toggle defaults to All, and a picked scope narrows the files read", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [artifact()] }),
  });
  open();

  await screen.findByText("notes.txt");
  const scopes = screen.getByRole("tablist", { name: "Scope" });
  expect(within(scopes).getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  expect(calls.some((url) => url.includes("scope="))).toBe(false);

  await userEvent.click(within(scopes).getByRole("tab", { name: "Created by me" }));
  await waitFor(() => expect(calls.some((url) => url.includes("scope=created"))).toBe(true));
  expect(calls.some((url) => url.includes("/objects/site") && url.includes("scope="))).toBe(false);
});

test("a picked scope reads from the first page, and a fresh screen opens on All", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [artifact()], older: "page-2" }),
  });
  const view = open();

  await userEvent.click(await screen.findByRole("button", { name: "Older" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after=page-2"))).toBe(true));

  await userEvent.click(screen.getByRole("tab", { name: "Created by me" }));
  await waitFor(() => expect(calls.some((url) => url.includes("scope=created"))).toBe(true));
  expect(
    calls.filter((url) => url.includes("scope=created")).some((url) => url.includes("after=")),
  ).toBe(false);

  /* The scope is a place, not a habit. A screen opened with nothing said about it stands on the
     default — the leading choice, already drawn — rather than on whatever was picked last. */
  view.unmount();
  calls.length = 0;
  open();

  const scopes = await screen.findByRole("tablist", { name: "Scope" });
  expect(within(scopes).getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  expect(calls.some((url) => url.includes("scope="))).toBe(false);
});

test("a link carrying a scope opens on it", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [artifact()] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" place={{ scope: "created" }} />
    </MainAgentProvider>,
  );

  const scopes = await screen.findByRole("tablist", { name: "Scope" });
  expect(
    within(scopes).getByRole("tab", { name: "Created by me" }).getAttribute("aria-selected"),
  ).toBe("true");
  await waitFor(() => expect(calls.some((url) => url.includes("scope=created"))).toBe(true));
});

test("a link naming a scope the toggle no longer offers reads as All", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [artifact()] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" place={{ scope: "shared" }} />
    </MainAgentProvider>,
  );

  await screen.findByText("notes.txt");
  const scopes = screen.getByRole("tablist", { name: "Scope" });
  expect(within(scopes).getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe(
    "true",
  );
  expect(calls.some((url) => url.includes("scope="))).toBe(false);
});

test("the scope toggle judges sites by their owner", async () => {
  const mineSite = { ...DOCS, name: "mine-site", owner_email: "member@example.com" };
  wire({
    "/objects/site": () => objectIndex(SITE_KIND, [mineSite, DOCS]),
    "/workspace/artifacts": () => json({ artifacts: [] }),
  });
  render(
    <Viewer.Provider value="member@example.com">
      <MainAgentProvider agents={[AGENT]}>
        <PlacedSection section="artifacts" />
      </MainAgentProvider>
    </Viewer.Provider>,
  );

  expect(await screen.findByText("mine-site")).toBeTruthy();
  expect(screen.getByText("docs-abc")).toBeTruthy();

  await userEvent.click(screen.getByRole("tab", { name: "Created by me" }));
  await waitFor(() => expect(screen.queryByText("docs-abc")).toBeNull());
  expect(screen.getByText("mine-site")).toBeTruthy();

  const scopes = screen.getByRole("tablist", { name: "Scope" });
  await userEvent.click(within(scopes).getByRole("tab", { name: "All" }));
  expect(await screen.findByText("docs-abc")).toBeTruthy();
  expect(screen.getByText("mine-site")).toBeTruthy();
});

const DOCS_DETAIL = {
  ...SITE_KIND,
  name: "docs-abc",
  summary: "docs · workspace · sandbox port 3000",
  spec: null,
  status: { visibility: "workspace", site_url: DOCS_URL, owner_email: "member@example.com" },
  links: [],
  created_at: "2026-07-01T09:00:00Z",
  updated_at: null,
};

test("an opened site leads its record with the live page, full width and interactive", async () => {
  wire({
    "/objects/site/docs-abc": () => json(DOCS_DETAIL),
    "/objects/site": () => objectIndex(SITE_KIND, [DOCS]),
    "/workspace/artifacts": () => json({ artifacts: [] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" place={{ opens: [DOCS_LANE] }} />
    </MainAgentProvider>,
  );

  const record = await screen.findByRole("region", { name: "docs-abc" });
  const frame = await within(record).findByTitle("docs-abc");
  expect(frame.tagName).toBe("IFRAME");
  expect(frame.getAttribute("src")).toBe(DOCS_URL);
  expect(frame.hasAttribute("sandbox")).toBe(false);
  expect(frame.className).not.toContain("pointer-events-none");
  expect(frame.style.transform).toContain("scale");
  const view = frame.parentElement;
  expect(view?.className).toContain("w-full");
});

test("an opened site the frame cannot reach leads with no frame", async () => {
  wire({
    "/objects/site/notes-def": () =>
      json({
        ...DOCS_DETAIL,
        name: "notes-def",
        summary: "notes · private · sandbox port 3001",
        status: { visibility: "private", owner_email: "member@example.com" },
      }),
    "/objects/site": () => objectIndex(SITE_KIND, [NOTES]),
    "/workspace/artifacts": () => json({ artifacts: [] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" place={{ opens: [NOTES_LANE] }} />
    </MainAgentProvider>,
  );

  const record = await screen.findByRole("region", { name: "notes-def" });
  await within(record).findByText("site");
  expect(record.querySelector("iframe")).toBeNull();
});

/** The page a lane stands on is read off the sites listing, which the screen holds whatever the
 *  shelf under it is narrowed to. A lane taking the address off the shelf's own cards would lose
 *  the frame the moment the member narrowed to a family the site is not a member of. */
test("a site's lane keeps its page while the shelf narrows away from it", async () => {
  wire({
    "/objects/site/docs-abc": () => json(DOCS_DETAIL),
    "/objects/site": () => objectIndex(SITE_KIND, [DOCS]),
    "/workspace/artifacts": () => json({ artifacts: [artifact()] }),
  });
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedSection section="artifacts" place={{ opens: [DOCS_LANE] }} />
    </MainAgentProvider>,
  );

  const record = await screen.findByRole("region", { name: "docs-abc" });
  expect((await within(record).findByTitle("docs-abc")).getAttribute("src")).toBe(DOCS_URL);

  await narrowTo("Documents");
  await waitFor(() => expect(tiles().map((band) => band.name)).toEqual(["notes.txt"]));
  const narrowed = screen.getByRole("region", { name: "docs-abc" });
  expect(within(narrowed).getByTitle("docs-abc").getAttribute("src")).toBe(DOCS_URL);
});
