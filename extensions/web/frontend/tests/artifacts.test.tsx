import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";
import { Workspace } from "@/views/Workspace";

import { AGENT, json, useStreamFake } from "./harness";

const TEXT_URL = "/dl/notes.txt";

const artifact = (over: Record<string, unknown> = {}) => ({
  id: "a1",
  filename: "notes.txt",
  subject: "notes",
  media_type: "text/plain",
  size_bytes: 12,
  created_at: "2026-07-31T09:00:00",
  url: TEXT_URL,
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("hello from the file")).toBeTruthy();
  expect(screen.getByText("notes · text/plain · 12 B · 2026-07-31 09:00")).toBeTruthy();

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByText("hello from the file")).toBeNull());
  expect(screen.getByRole("button", { name: "notes.txt" })).toBeTruthy();
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("hello from the file")).toBeTruthy();

  expect(screen.getByRole("button", { name: "notes.txt" })).toBeTruthy();
});

test("leaving the view takes the viewer with it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      if (url.includes("/workspace/memory"))
        return json({ available: true, kinds: [], matches: [], older: null, newer: null });
      return new Response("hello from the file");
    }),
  );
  const view = render(
    <MainAgentProvider agents={[AGENT]}>
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("hello from the file")).toBeTruthy();

  view.rerender(
    <MainAgentProvider agents={[AGENT]}>
      <Workspace view="memory" />
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "shot.png" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "bundle.zip" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  expect(await screen.findByText("notes.txt")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "notes.txt" })).toBeNull();
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
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
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  await waitFor(() => expect(releaseFirst).not.toBeNull());
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  releaseFirst!(new Response("the first body"));
  await new Promise((resolve) => setTimeout(resolve, 0));

  expect(screen.queryByText("the first body")).toBeNull();
  expect(screen.getByRole("button", { name: "notes.txt" })).toBeTruthy();
});

test("the artifacts listing renders as rows — name, meta, date — with no table", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [artifact()] });
      return new Response("x");
    }),
  );
  render(
    <MainAgentProvider agents={[AGENT]}>
      <Workspace view="artifacts" />
    </MainAgentProvider>,
  );

  const item = (await screen.findAllByRole("listitem")).find((entry) =>
    entry.textContent?.includes("notes.txt"),
  );
  expect(item?.querySelector('[data-part="primary"]')?.textContent).toBe("notes.txt");
  expect(item?.querySelector('[data-part="meta"]')?.textContent).toBe("notes · text/plain · 12 B");
  expect(item?.querySelector('[data-part="when"]')?.textContent).toBe("2026-07-31 09:00");
  expect(screen.queryAllByRole("columnheader")).toEqual([]);
});
