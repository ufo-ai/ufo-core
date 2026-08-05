import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import {
  changesHash,
  parseHash,
  subagentConversationHash,
  subagentHash,
  workspaceHash,
} from "@/lib/route";

import { AGENT, CONVO_ID, MEMBER, json, useStreamFake } from "./harness";

const OLDER = {
  id: "a1",
  filename: "notes.txt",
  subject: "notes",
  media_type: "text/plain",
  size_bytes: 12,
  created_at: "2026-07-30T09:00:00",
  url: "/dl/notes.txt",
};

const NEWER = { ...OLDER, id: "a2", filename: "report.txt", created_at: "2026-07-31T09:00:00" };

const OLDER_KEY = OLDER.created_at + "|" + OLDER.filename;

beforeEach(() => {
  useStreamFake();
});

function serve() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("/workspace/artifacts")) {
        const paged = url.includes("after=");
        return json({
          artifacts: paged ? [OLDER] : [NEWER],
          older: paged ? null : "c-older",
          newer: paged ? "c-newer" : null,
        });
      }
      if (url.includes("/workspace/memory")) {
        return json({ available: true, kinds: ["fact", "profile"], matches: [] });
      }
      if (url.includes("/workspace/sources")) {
        return json({
          sources: [
            {
              name: "notion-main",
              backend: "notion",
              stream: "pages",
              account_id: "acct",
              base_url: null,
              owner_email: "member@example.com",
              shared: false,
              consecutive_errors: 0,
              next_sync_at: "2026-08-01T06:00:00",
            },
            {
              name: null,
              backend: "rss",
              stream: "feed",
              account_id: null,
              base_url: "https://example.com/feed",
              owner_email: null,
              shared: true,
              consecutive_errors: 0,
              next_sync_at: "2026-08-02T09:30:00",
            },
          ],
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return new Response("file body");
    }),
  );
  return calls;
}

test("the workspace hash carries its place and parses back to it", () => {
  const place = { kind: "fact", after: "c2", q: "roadmap", chip: "Shared", open: OLDER_KEY };
  const route = parseHash(workspaceHash("memory", place));
  expect(route).toEqual({ kind: "workspace", view: "memory", place });
  expect(parseHash(workspaceHash("artifacts"))).toEqual({
    kind: "workspace",
    view: "artifacts",
    place: {},
  });
});

test("the changes hash names its conversation", () => {
  expect(parseHash(changesHash(AGENT.id, CONVO_ID))).toEqual({
    kind: "changes",
    agentId: AGENT.id,
    conversationId: CONVO_ID,
  });
  const root = "99999999-9999-4999-8999-999999999999";
  expect(parseHash(changesHash(AGENT.id, CONVO_ID, root))).toEqual({
    kind: "changes",
    agentId: AGENT.id,
    conversationId: CONVO_ID,
    rootConversationId: root,
  });
});

test("the subagent conversation hash names its run and lands on the conversations tab", () => {
  expect(parseHash(subagentConversationHash("deep_research", CONVO_ID))).toEqual({
    kind: "subagent",
    name: "deep_research",
    tab: "conversations",
    conversationId: CONVO_ID,
  });
  expect(parseHash(subagentHash("deep_research", "conversations"))).toEqual({
    kind: "subagent",
    name: "deep_research",
    tab: "conversations",
  });
});

test("a reload lands on the page and the open artifact the hash names", async () => {
  location.hash = workspaceHash("artifacts", { after: "c-older", open: OLDER_KEY });
  const calls = serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("file body")).toBeTruthy();
  expect(calls.some((url) => url.includes("after=c-older"))).toBe(true);
  expect(screen.getByRole("button", { name: "Close" })).toBeTruthy();
});

test("paging writes the cursor to the hash and Back steps to the previous page", async () => {
  location.hash = workspaceHash("artifacts");
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "Older" }));

  expect(await screen.findByRole("button", { name: "notes.txt" })).toBeTruthy();
  expect(location.hash).toContain("after=c-older");

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/workspace/artifacts"));
  expect(await screen.findByRole("button", { name: "report.txt" })).toBeTruthy();
});

test("opening an artifact names it in the hash and closing clears it", async () => {
  location.hash = workspaceHash("artifacts", { after: "c-older" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toContain("open=");

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(location.hash).not.toContain("open="));
  expect(location.hash).toContain("after=c-older");
  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());
});

test("search and chip ride the hash by replacement, never as history entries", async () => {
  location.hash = "#/agents";
  location.hash = workspaceHash("sources");
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "Shared 1" }));
  await userEvent.type(screen.getByRole("searchbox"), "rss");

  await waitFor(() => expect(location.hash).toContain("q=rss"));
  expect(location.hash).toContain("chip=Shared");

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
});

test("a reloaded filter lands filtered", async () => {
  location.hash = workspaceHash("sources", { q: "rss", chip: "Shared" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("rss")).toBeTruthy();
  expect(screen.queryByText("notion")).toBeNull();
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("rss");
  expect(screen.getByRole("button", { name: /Shared/ }).getAttribute("aria-pressed")).toBe(
    "true",
  );
});

test("a tab click releases the filters, so returning starts unfiltered", async () => {
  location.hash = workspaceHash("sources", { q: "rss", chip: "Shared" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("rss");

  await userEvent.click(screen.getByRole("tab", { name: "Team" }));
  await waitFor(() => expect(location.hash).toBe("#/workspace/team"));
  await userEvent.click(screen.getByRole("tab", { name: "Sources" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/sources"));
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
  expect(await screen.findByText("notion")).toBeTruthy();
});

test("closing the viewer unwinds the entry opening it pushed", async () => {
  location.hash = "#/agents";
  location.hash = workspaceHash("artifacts");
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "report.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();
  expect(location.hash).toContain("open=");
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(location.hash).toBe("#/workspace/artifacts"));

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
  expect(screen.queryByText("file body")).toBeNull();
});

test("a cursor the surface refuses leaves a way back to the first page", async () => {
  location.hash = workspaceHash("artifacts", { after: "not-a-cursor" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("after=")) return new Response("malformed listing cursor", { status: 400 });
      if (url.includes("/workspace/artifacts")) return json({ artifacts: [NEWER], older: null });
      if (url.includes("/api/chats")) return json({ chats: [] });
      return json({ messages: [] });
    }),
  );
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "First page" }));

  expect(await screen.findByRole("button", { name: "report.txt" })).toBeTruthy();
  expect(location.hash).toBe("#/workspace/artifacts");
});

test("a second row opened behind the sheet still closes to the listing", async () => {
  location.hash = "#/agents";
  location.hash = workspaceHash("artifacts", { after: "c-older" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "notes.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "notes.txt" }));
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(location.hash).not.toContain("open="));
  expect(screen.queryByText("file body")).toBeNull();
  expect(location.hash).toContain("after=c-older");

  history.back();
  await waitFor(() => expect(location.hash).toBe("#/agents"));
});

test("paging away from an open row carries no dead open key", async () => {
  location.hash = workspaceHash("artifacts");
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "report.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Older" }));

  expect(await screen.findByRole("button", { name: "notes.txt" })).toBeTruthy();
  expect(location.hash).not.toContain("open=");
  expect(screen.queryByText("That item is not on this page.")).toBeNull();
});

test("a placement from a pane the member already left never writes its dead place back", async () => {
  let release: ((value: Response) => void) | null = null;
  location.hash = workspaceHash("sources", { q: "rss" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/intents")) {
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      if (url.includes("/workspace/sources")) {
        return json({
          sources: [
            {
              name: "rss-feed",
              backend: "rss",
              stream: "feed",
              account_id: null,
              base_url: "https://example.com/feed",
              owner_email: null,
              shared: true,
              consecutive_errors: 0,
              next_sync_at: "2026-08-01T06:00:00",
            },
          ],
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return json({ members: [], can_add: false, domain: null });
    }),
  );
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));
  await waitFor(() => expect(release).not.toBeNull());
  await userEvent.click(screen.getByRole("tab", { name: "Team" }));
  await userEvent.click(screen.getByRole("tab", { name: "Sources" }));
  await waitFor(() => expect(location.hash).toBe("#/workspace/sources"));

  release!(json({ applied: true, message: "Resync queued." }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(location.hash).toBe("#/workspace/sources");
  expect(((await screen.findByRole("searchbox")) as HTMLInputElement).value).toBe("");
  expect(screen.queryByText("Resync queued.")).toBeNull();
});

test("a refused memory cursor leaves a way back to the first page", async () => {
  location.hash = workspaceHash("memory", { after: "not-a-cursor" });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("after=")) return new Response("malformed listing cursor", { status: 400 });
      if (url.includes("/workspace/memory")) {
        return json({ available: true, kinds: ["fact"], matches: [] });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      return json({ messages: [] });
    }),
  );
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "First page" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/memory"));
  expect(await screen.findByText("No memories yet.")).toBeTruthy();
});

test("a hash naming a filter or a memory class that does not exist says so", async () => {
  location.hash = workspaceHash("sources", { chip: "Nonexistent" });
  serve();
  const view = render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("That filter is not available.")).toBeTruthy();
  view.unmount();

  location.hash = workspaceHash("memory", { kind: "nonexistent" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);
  expect(await screen.findByText("That memory class is not available.")).toBeTruthy();
});

test("an intent resolving after the member leaves never rewrites where they went", async () => {
  let release: ((value: Response) => void) | null = null;
  location.hash = workspaceHash("sources");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/intents")) {
        return new Promise<Response>((resolve) => {
          release = resolve;
        });
      }
      if (url.includes("/workspace/sources")) {
        return json({
          sources: [
            {
              name: "notion-main",
              backend: "notion",
              stream: "pages",
              account_id: "acct",
              base_url: null,
              owner_email: "member@example.com",
              shared: false,
              consecutive_errors: 0,
              next_sync_at: "2026-08-01T06:00:00",
            },
          ],
        });
      }
      if (url.includes("/api/chats")) return json({ chats: [] });
      if (url.includes("/transcript")) return json({ messages: [] });
      return json({ members: [], can_add: false, domain: null });
    }),
  );
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "Resync" }));
  await waitFor(() => expect(release).not.toBeNull());
  await userEvent.click(screen.getByRole("tab", { name: "Team" }));
  await waitFor(() => expect(location.hash).toBe("#/workspace/team"));

  release!(json({ applied: true, message: "Resync queued." }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(location.hash).toBe("#/workspace/team");
});

test("a memory search and kind filter ride the hash and survive reload", async () => {
  location.hash = workspaceHash("memory");
  const calls = serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await userEvent.click(await screen.findByRole("button", { name: "fact" }));
  expect(location.hash).toBe("#/workspace/memory?kind=fact");
  await waitFor(() => expect(calls.some((url) => url.includes("kind=fact"))).toBe(true));

  await userEvent.type(screen.getByPlaceholderText("Search memory…"), "roadmap");
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
  expect(location.hash).toBe("#/workspace/memory?kind=fact&q=roadmap");
  await waitFor(() => expect(calls.some((url) => url.includes("q=roadmap"))).toBe(true));
});

test("a reloaded kind filter lands on that kind and reads it", async () => {
  location.hash = workspaceHash("memory", { kind: "fact" });
  const calls = serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  const chip = await screen.findByRole("button", { name: "fact" });
  expect(chip.getAttribute("aria-current")).toBe("true");
  expect(screen.getByRole("button", { name: "all" }).getAttribute("aria-current")).toBe("false");
  expect(calls.some((url) => url.includes("/workspace/memory?kind=fact"))).toBe(true);
});

test("a search cleared from a kind returns to that kind rather than page one", async () => {
  location.hash = workspaceHash("memory", { kind: "fact", q: "roadmap" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  const box = (await screen.findByPlaceholderText("Search memory…")) as HTMLInputElement;
  expect(box.value).toBe("roadmap");
  await userEvent.clear(box);
  await userEvent.click(screen.getByRole("button", { name: "Search" }));

  await waitFor(() => expect(location.hash).toBe("#/workspace/memory?kind=fact"));
  expect(screen.getByRole("button", { name: "fact" }).getAttribute("aria-current")).toBe("true");
});

test("a deep link naming a row that is not on this page says so", async () => {
  location.hash = workspaceHash("artifacts", { open: "2020-01-01T00:00:00|gone.txt" });
  serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByText("That item is not on this page.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  expect(await screen.findByRole("button", { name: "report.txt" })).toBeTruthy();
});

test("opening and closing the viewer issues no second listing read", async () => {
  location.hash = workspaceHash("artifacts");
  const calls = serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  await screen.findByRole("button", { name: "report.txt" });
  const reads = () => calls.filter((url) => url.includes("/workspace/artifacts")).length;
  const before = reads();

  await userEvent.click(screen.getByRole("button", { name: "report.txt" }));
  expect(await screen.findByText("file body")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByText("file body")).toBeNull());

  expect(reads()).toBe(before);
});

test("a reloaded memory search prefills the box and fetches the query", async () => {
  location.hash = workspaceHash("memory", { q: "roadmap" });
  const calls = serve();
  render(<App agents={[AGENT]} subagents={[]} member={MEMBER} />);

  expect(await screen.findByDisplayValue("roadmap")).toBeTruthy();
  expect(calls.some((url) => url.includes("/workspace/memory?q=roadmap"))).toBe(true);
});
