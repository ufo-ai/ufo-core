import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { Viewer } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";

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

  expect(await screen.findByRole("tab", { name: "Images" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Documents" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Data" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Other" })).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Images" }));
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

  await userEvent.click(screen.getByRole("tab", { name: "Images" }));
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
  const sheet = screen.getByRole("dialog");
  expect(within(sheet).getByText(/^notes · member@example.com/).textContent).toBe(
    "notes · member@example.com · text/plain · 12 B · Jul 31 2026",
  );

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
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
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
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
  const full = document.querySelector("aside img, [role='dialog'] img") as HTMLImageElement;
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
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  releaseFirst!(new Response("the first body"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("the first body")).toBeNull();
  expect(await viewCard("notes.txt")).toBeTruthy();
});

test("the artifacts listing renders as cards, each led by its own band", async () => {
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
  expect(item?.querySelector('[data-part="body"]')?.textContent).toBe("notes");
  expect(item?.querySelector('[data-part="meta"]')?.textContent).toBe("member@example.com");
  expect(item?.querySelector('[data-part="status"]')?.textContent).toBe("Jul 31 2026");
  const band = item?.querySelector('[data-part="mark"]');
  expect(band?.className).toContain("h-(--size-band)");
  expect(band?.querySelector("img")).toBeNull();
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

test("an image artifact fills its band, and a dead link leaves the placeholder", async () => {
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
  const sheet = document.querySelector("aside, [role='dialog']") as HTMLElement;
  await waitFor(() => expect(within(sheet).getAllByRole("table").length).toBeGreaterThan(0));
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
  const full = document.querySelector(
    "aside img[src='/dl/report-preview.png'], [role='dialog'] img[src='/dl/report-preview.png']",
  );
  expect(full).not.toBeNull();
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

test("a file card binds its parts to the artifact payload", async () => {
  shelf([], [artifact({ filename: "report.txt", subject: "member@example.com" })]);
  open();

  const card = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("report.txt"),
  );
  expect(card?.querySelector('[data-part="primary"]')?.textContent).toBe("report.txt");
  expect(card?.querySelector('[data-part="body"]')?.textContent).toBe("member@example.com");
  expect(card?.querySelector('[data-part="status"]')?.textContent).toBe("Jul 31 2026");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});

test("a site is a card carrying its date, type, visibility, and summary", async () => {
  shelf([DOCS], []);
  open();

  const card = (await screen.findAllByRole("listitem"))[0];
  expect(card.querySelector('[data-part="primary"]')?.textContent).toBe("docs-abc");
  expect(card.querySelector('[data-part="status"]')?.textContent).toBe("Jul 1 2026");
  expect(card.querySelector('[data-part="meta"]')?.textContent).toBe(
    "Site · Workspace · Workspace",
  );
  expect(card.querySelector('[data-part="body"]')?.textContent).toBe(DOCS.summary);
  const band = card.querySelector('[data-part="mark"]');
  expect(band?.className).toContain("h-(--size-band)");
  expect(band?.getAttribute("aria-hidden")).toBe("true");
});

test("a site with a link opens it in a new tab, and one without draws no Open", async () => {
  shelf([DOCS, NOTES], []);
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

test("a continued file page clamps sites to that page's date window", async () => {
  const latest = { ...DOCS, name: "latest-site", created_at: "2026-07-31T08:00:00Z" };
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, [latest, DOCS]),
    "/workspace/artifacts": (url) =>
      url.includes("after=")
        ? json({ artifacts: [artifact({ filename: "older.txt", created_at: DOCS.created_at })] })
        : json({ artifacts: [artifact()], older: cursorAt("2026-07-31T09:00:00") }),
  });
  open();

  expect(await screen.findByText("latest-site")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after="))).toBe(true));
  expect(await screen.findByText("docs-abc")).toBeTruthy();
  expect(screen.queryByText("latest-site")).toBeNull();
});

test("a site between two file pages stands on the older one, never on neither", async () => {
  const between = { ...DOCS, name: "between-site", created_at: "2026-07-31T08:30:00Z" };
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, [between]),
    "/workspace/artifacts": (url) =>
      url.includes("after=")
        ? json({ artifacts: [artifact({ filename: "older.txt", created_at: "2026-07-31T08:00:00Z" })] })
        : json({
            artifacts: [artifact({ created_at: "2026-07-31T09:00:00Z" })],
            older: cursorAt("2026-07-31T09:00:00Z"),
          }),
  });
  open();

  await screen.findByText("notes.txt");
  expect(screen.queryByText("between-site")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after="))).toBe(true));
  await screen.findByText("older.txt");
  expect(screen.getByText("between-site")).toBeTruthy();
});

test("walking back to a page keeps its sites, which a foot cursor read as a top would drop", async () => {
  const recent = { ...DOCS, name: "recent-site", created_at: "2026-07-31T10:00:00Z" };
  const page1 = { artifacts: [artifact()], older: cursorAt("2026-07-31T09:00:00") };
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, [recent]),
    "/workspace/artifacts": (url) =>
      url.includes("after=older")
        ? json({
            artifacts: [artifact({ filename: "older.txt", created_at: DOCS.created_at })],
            newer: cursorAt("2026-07-31T09:00:00", "newer"),
          })
        : json(page1),
  });
  open();

  expect(await screen.findByText("recent-site")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));
  expect(await screen.findByText("older.txt")).toBeTruthy();
  expect(screen.queryByText("recent-site")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Newer" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after=newer"))).toBe(true));
  await screen.findByText("notes.txt");
  expect(screen.getByText("recent-site")).toBeTruthy();
});

test("the family tabs narrow the shelf to sites or to one media kind", async () => {
  const { calls } = shelf([DOCS], [artifact()]);
  open();

  await screen.findByText("docs-abc");
  await userEvent.click(screen.getByRole("tab", { name: "Sites" }));

  expect(screen.getByRole("tab", { name: "Sites" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByText("notes.txt")).toBeNull();

  await userEvent.click(screen.getByRole("tab", { name: "Documents" }));
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
  expect(screen.queryByRole("tab", { name: "Sites" })).toBeNull();
  expect(screen.getByRole("tab", { name: "Images" })).toBeTruthy();
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

test("a picked scope reads from the first page and is remembered across mounts", async () => {
  const { calls } = wire({
    "/objects/site": () => objectIndex(SITE_KIND, []),
    "/workspace/artifacts": () => json({ artifacts: [artifact()], older: "page-2" }),
  });
  const view = open();

  await userEvent.click(await screen.findByRole("button", { name: "Older" }));
  await waitFor(() => expect(calls.some((url) => url.includes("after=page-2"))).toBe(true));

  await userEvent.click(screen.getByRole("tab", { name: "Shared with me" }));
  await waitFor(() => expect(calls.some((url) => url.includes("scope=shared"))).toBe(true));
  expect(
    calls.filter((url) => url.includes("scope=shared")).some((url) => url.includes("after=")),
  ).toBe(false);

  view.unmount();
  calls.length = 0;
  open();

  const held = await screen.findByRole("tab", { name: "Shared with me" });
  expect(held.getAttribute("aria-selected")).toBe("true");
  await waitFor(() => expect(calls.some((url) => url.includes("scope=shared"))).toBe(true));
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

  await userEvent.click(screen.getByRole("tab", { name: "Shared with me" }));
  expect(await screen.findByText("docs-abc")).toBeTruthy();
  expect(screen.queryByText("mine-site")).toBeNull();
});
