import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  PlacedSection,
  SITE_KIND,
  json,
  objectIndex,
  useStreamFake,
  viewCard,
  wire,
} from "./harness";

const TEXT_URL = "/dl/notes.txt";

const artifact = (over: Record<string, unknown> = {}) => ({
  id: "a1",
  filename: "notes.txt",
  subject: "notes",
  media_type: "text/plain",
  size_bytes: 12,
  created_at: "2026-07-31T09:00:00",
  url: TEXT_URL,
  owner_email: "member@example.com",
  origin: null,
  conversation_id: "c1",
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

beforeEach(() => {
  useStreamFake();
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
  expect(
    screen.getByText("notes · member@example.com · text/plain · 12 B · Jul 31 2026"),
  ).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByText("hello from the file")).toBeNull());
  expect(await viewCard("notes.txt")).toBeTruthy();
});

test("the listing stays reachable behind an open viewer", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
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
      if (url.includes("/objects/site")) return objectIndex(SITE_KIND, []);
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
      <PlacedSection section="sites" />
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => json({ artifacts: [artifact({ media_type: "image/png", filename: "shot.png" })] })),
  );
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json({ artifacts: [artifact({ media_type: "application/zip", filename: "bundle.zip" })] }),
    ),
  );
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

test("an artifact with no link stays plain text and opens nothing", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => json({ artifacts: [artifact({ url: null })] })));
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      json({ artifacts: [artifact({ media_type: "image/png", filename: "shot.png" })] }),
    ),
  );
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
