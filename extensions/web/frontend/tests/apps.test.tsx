import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { AppInit, Placement } from "@/apps/kit";
import { endpointFor } from "@/lib/bridge";

import { json, objectIndex, wire, AGENT, CONVO_ID, MEMBER, TASK_KIND, TURN_ID } from "./harness";

let nextAnimationFrame = 0;
const animationFrames = new Map<number, FrameRequestCallback>();
window.requestAnimationFrame = (callback) => {
  const id = ++nextAnimationFrame;
  animationFrames.set(id, callback);
  queueMicrotask(() => {
    const held = animationFrames.get(id);
    animationFrames.delete(id);
    held?.(performance.now());
  });
  return id;
};
window.cancelAnimationFrame = (id) => void animationFrames.delete(id);
const applicationLifecycle = await import("@/apps/lifecycle");
vi.doMock("@/apps/lifecycle", () => applicationLifecycle);

/** jsdom's `window.top` is the window itself, so the runtime's posts land on our own listener. Each
 *  test re-imports the module: the correlation maps and the held init are module state. */

type Runtime = typeof import("@/apps/runtime");

const INIT = {
  member: { email: MEMBER.email, admin: true },
  agents: [AGENT],
  agentId: AGENT.id,
  banded: false,
  place: {},
  portal: location.origin,
};

function shell(
  onCall: (message: Record<string, unknown>) => void,
  handshake: AppInit = INIT,
): () => void {
  const listener = (event: MessageEvent) => {
    const message = event.data as { ufo?: string } | null;
    if (!message || typeof message.ufo !== "string") return;
    if (message.ufo === "ready") {
      window.postMessage({ ufo: "init", ...handshake }, "*");
      return;
    }
    if (message.ufo === "call" || message.ufo === "close") {
      onCall(message as Record<string, unknown>);
    }
  };
  window.addEventListener("message", listener);
  return () => window.removeEventListener("message", listener);
}

async function connected(onCall: (message: Record<string, unknown>) => void): Promise<Runtime> {
  vi.resetModules();
  const runtime = (await import("@/apps/runtime")) as Runtime;
  const detach = shell(onCall);
  const init = await runtime.connect();
  expect(init.agentId).toBe(AGENT.id);
  cleanups.push(detach);
  return runtime;
}

const BEYOND_READY_BUDGET_MS = 5000;
const cleanups: (() => void)[] = [];
const nativeFetch = window.fetch;
const nativeEventSource = window.EventSource;
const shellSetTimeout = window.setTimeout.bind(window);

type LifecycleController = {
  snapshot(): {
    generation: number;
    mounted: boolean;
    state: string;
    revision: number;
    blockingWork: number;
    blocking: Record<string, number>;
  };
  afterPaint(): Promise<void>;
  beginObservation(): number;
  endObservation(epoch: number): Promise<void>;
};

function lifecycle(): LifecycleController {
  return (window as unknown as { __ufoApplicationLifecycle: LifecycleController })
    .__ufoApplicationLifecycle;
}

async function painted(): Promise<void> {
  await new Promise<void>((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
  );
}

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
  window.fetch = nativeFetch;
  window.EventSource = nativeEventSource;
});

test("a portal fetch rides the bridge and comes back as the surface's own response", async () => {
  const runtime = await connected((message) => {
    expect(message.method).toBe("GET");
    expect(message.path).toBe("/objects/conversation");
    window.postMessage(
      {
        ufo: "data",
        id: message.id,
        ok: true,
        status: 200,
        body: JSON.stringify({ objects: [] }),
        refusal: null,
        fault: null,
      },
      "*",
    );
  });
  runtime.installShims();
  const res = await fetch("/surface/web/objects/conversation");
  expect(res.status).toBe(200);
  expect(await res.json()).toEqual({ objects: [] });
});

test("a refused call reads as a refusal response, marked so the reader shows its sentence", async () => {
  const runtime = await connected((message) => {
    window.postMessage(
      {
        ufo: "data",
        id: message.id,
        ok: false,
        status: 403,
        body: "This record is not yours to change.",
        refusal: "This record is not yours to change.",
        fault: null,
      },
      "*",
    );
  });
  runtime.installShims();
  const res = await fetch("/surface/web/objects/scheduled_task", {
    method: "POST",
    body: JSON.stringify({ name: "x", spec: {}, agent: AGENT.id }),
  });
  expect(res.status).toBe(403);
  expect(res.headers.get("x-ufo-refusal")).toBe("This record is not yours to change.");
  expect(await res.text()).toBe("This record is not yours to change.");
});

test("stated headers ride the call message, and a fetch off the portal keeps the native path", async () => {
  const calls: Record<string, unknown>[] = [];
  const runtime = await connected((message) => {
    calls.push(message);
    window.postMessage(
      { ufo: "data", id: message.id, ok: true, status: 200, body: "{}", refusal: null, fault: null },
      "*",
    );
  });
  runtime.installShims();
  await fetch("/surface/web/agents/" + AGENT.id + "/chat?conversation=new", {
    method: "POST",
    body: "hello",
    headers: { "x-ufo-timezone": "UTC", authorization: "Bearer nope" },
  });
  expect(calls[0].headers).toEqual({ "x-ufo-timezone": "UTC", authorization: "Bearer nope" });

  const native = vi.fn(async () => new Response("elsewhere"));
  window.fetch = native as unknown as typeof fetch;
  runtime.installShims();
  await fetch("https://example.com/data");
  expect(native).toHaveBeenCalled();
});

test("an attachment send decomposes into parts the message can carry", async () => {
  const calls: Record<string, unknown>[] = [];
  const runtime = await connected((message) => {
    calls.push(message);
    window.postMessage(
      { ufo: "data", id: message.id, ok: true, status: 200, body: "{}", refusal: null, fault: null },
      "*",
    );
  });
  runtime.installShims();
  const form = new FormData();
  form.append("message", "hello");
  form.append("files", new File(["bytes"], "notes.txt", { type: "text/plain" }));
  const res = await fetch("/surface/web/agents/" + AGENT.id + "/chat?conversation=new", {
    method: "POST",
    body: form,
  });
  expect(res.status).toBe(200);
  const sent = calls[0].form as { name: string; value: string | File }[];
  expect(sent.map((part) => part.name)).toEqual(["message", "files"]);
  expect(sent[0].value).toBe("hello");
  expect((sent[1].value as File).name).toBe("notes.txt");
  expect(calls[0].body).toBeUndefined();
});

test("an opened document's page render rides as parts the shell's table admits", async () => {
  const calls: Record<string, unknown>[] = [];
  const runtime = await connected((message) => {
    calls.push(message);
    window.postMessage(
      {
        ufo: "data",
        id: message.id,
        ok: true,
        status: 200,
        body: JSON.stringify({ start_page: 1, page_count: 3, pages: ["cGFnZQ=="] }),
        refusal: null,
        fault: null,
      },
      "*",
    );
  });
  runtime.installShims();
  const form = new FormData();
  form.append("file", new Blob(["%PDF-1.7"], { type: "application/pdf" }), "report.pdf");
  form.append("start_page", "1");
  const res = await fetch("/surface/web/preview", { method: "POST", body: form });
  expect(await res.json()).toMatchObject({ page_count: 3 });
  expect(endpointFor(calls[0].method as string, calls[0].path as string)).toBeTruthy();
  const sent = calls[0].form as { name: string; value: string | File }[];
  expect((sent[0].value as File).name).toBe("report.pdf");
  expect(sent[1].value).toBe("1");
});

test("a credential form's urlencoded body rides as its string", async () => {
  const calls: Record<string, unknown>[] = [];
  const runtime = await connected((message) => {
    calls.push(message);
    window.postMessage(
      { ufo: "data", id: message.id, ok: true, status: 200, body: "{}", refusal: null, fault: null },
      "*",
    );
  });
  runtime.installShims();
  const res = await fetch("/surface/web/credentials", {
    method: "POST",
    body: new URLSearchParams({ sealed: "s1", slot: "token", value: "secret words" }),
  });
  expect(res.status).toBe(200);
  expect(calls[0].body).toBe("sealed=s1&slot=token&value=secret+words");
  expect(calls[0].form).toBeUndefined();
});

test("a stream is an EventSource: open, named frames, and an end the page did not ask for is an error", async () => {
  const streamCalls: Record<string, unknown>[] = [];
  const runtime = await connected((message) => streamCalls.push(message));
  runtime.installShims();
  const source = new EventSource("/surface/web/turns/t1/stream");
  await vi.waitFor(() => expect(streamCalls).toHaveLength(1));
  const id = streamCalls[0].id;
  expect(streamCalls[0].path).toBe("/turns/t1/stream");

  const seen: string[] = [];
  source.onopen = () => seen.push("open");
  source.onmessage = (event) => seen.push("message:" + event.data);
  source.addEventListener("terminal", (event) => seen.push("terminal:" + (event as MessageEvent).data));
  source.onerror = () => seen.push("error:" + source.readyState);

  window.postMessage({ ufo: "opened", id }, "*");
  await vi.waitFor(() => expect(seen).toContain("open"));
  expect(source.readyState).toBe(EventSource.OPEN);
  window.postMessage({ ufo: "frame", id, event: "message", data: '{"text":"hi"}' }, "*");
  window.postMessage({ ufo: "frame", id, event: "terminal", data: '{"status":"done"}' }, "*");
  window.postMessage({ ufo: "end", id }, "*");
  await vi.waitFor(() => expect(seen).toContain("error:2"));
  expect(seen).toEqual(['open', 'message:{"text":"hi"}', 'terminal:{"status":"done"}', "error:2"]);
});

test("closing the source tells the shell and swallows the end that follows", async () => {
  const messages: Record<string, unknown>[] = [];
  const runtime = await connected((message) => messages.push(message));
  runtime.installShims();
  const source = new EventSource("/surface/web/turns/t2/stream");
  await vi.waitFor(() => expect(messages).toHaveLength(1));
  const id = messages[0].id;
  let errored = false;
  source.onerror = () => {
    errored = true;
  };
  source.close();
  await vi.waitFor(() =>
    expect(messages.some((message) => message.ufo === "close" && message.id === id)).toBe(true),
  );
  window.postMessage({ ufo: "end", id }, "*");
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(errored).toBe(false);
  expect(source.readyState).toBe(EventSource.CLOSED);
});

test("a throwing stream error remains visible and releases lifecycle work", async () => {
  const messages: Record<string, unknown>[] = [];
  const runtime = await connected((message) => messages.push(message));
  runtime.installShims();
  const source = new EventSource("/surface/web/turns/t3/stream");
  await vi.waitFor(() => expect(messages).toHaveLength(1));
  const failures: string[] = [];
  const onWindowError = (event: ErrorEvent) => {
    failures.push(event.message);
    event.preventDefault();
  };
  window.addEventListener("error", onWindowError);
  cleanups.push(() => window.removeEventListener("error", onWindowError));
  source.onerror = () => {
    throw new Error("stream handler failed");
  };

  window.postMessage({ ufo: "end", id: messages[0].id }, "*");

  await vi.waitFor(() => expect(source.readyState).toBe(EventSource.CLOSED));
  await painted();
  expect(failures).toEqual(["stream handler failed"]);
  expect(lifecycle().snapshot().blocking.stream).toBe(0);
});

test("a section app hosts a screen inside the portal's own section chrome", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const { ObjectPane } = await import("@/kernel/objects");
  const { MainAgentProvider } = await import("@/lib/mainAgent");
  const { Viewer } = await import("@/lib/audience");
  wire({
    "/objects/scheduled_task": () => objectIndex(TASK_KIND, []),
  });
  render(
    <Viewer.Provider value={MEMBER.email}>
      <MainAgentProvider agents={[AGENT]}>
        <SectionApp
          tab="wiki"
          init={{
            member: { email: MEMBER.email, admin: true },
            agents: [AGENT],
            agentId: AGENT.id,
            banded: false,
            place: {},
            portal: location.origin,
          }}
          view={{
            label: "Wiki",
            remountOnPlace: false,
            render: (place, onPlace) => (
              <ObjectPane
                agentId={null}
                kind="scheduled_task"
                opens={place.opens ?? []}
                onPlace={onPlace}
              />
            ),
          }}
        />
      </MainAgentProvider>
    </Viewer.Provider>,
  );
  expect(await screen.findByRole("heading", { name: "Wiki" })).toBeTruthy();
});

test("a banded section app draws no band and keeps the view's act as a row over the body", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const { usePageAct } = await import("@/kernel/pane");
  function Acted() {
    const act = usePageAct(<button type="button">Rebuild entries</button>);
    return (
      <>
        {act}
        <p>Entries</p>
      </>
    );
  }
  const app = (banded: boolean) => (
    <SectionApp
      tab="radar"
      init={{ ...INIT, banded }}
      view={{ label: "Radar", remountOnPlace: false, render: () => <Acted /> }}
    />
  );

  const view = render(app(true));
  expect(await screen.findByText("Entries")).toBeTruthy();
  expect(screen.queryByRole("heading")).toBeNull();
  expect(document.querySelector("[data-slot=header]")).toBeNull();
  const row = screen.getByRole("button", { name: "Rebuild entries" }).closest("[data-slot=page-acts]");
  expect(row).not.toBeNull();
  expect(document.querySelectorAll("[data-slot=page-acts]")).toHaveLength(1);

  view.rerender(app(false));
  expect(await screen.findByRole("heading", { level: 1, name: "Radar" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Rebuild entries" }).closest("[data-slot=header]"),
  ).not.toBeNull();
  expect(document.querySelector("[data-slot=page-acts]")).toBeNull();
});

test("a banded section app keeps an owning view's band acts and draws no name", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const { Header, usePageHead } = await import("@/kernel/pane");
  function Headed() {
    const band = usePageHead(
      <Header pinned heading={1} title="Radar" acts={<button type="button">Rebuild entries</button>} />,
    );
    return (
      <>
        {band}
        <p>Entries</p>
      </>
    );
  }
  const app = (banded: boolean) => (
    <SectionApp
      tab="radar"
      init={{ ...INIT, banded }}
      view={{ label: "Radar", remountOnPlace: false, ownsHeader: true, render: () => <Headed /> }}
    />
  );

  const view = render(app(true));
  expect(await screen.findByText("Entries")).toBeTruthy();
  expect(screen.queryByRole("heading")).toBeNull();
  expect(document.querySelector("[data-slot=header]")).toBeNull();
  const row = screen.getByRole("button", { name: "Rebuild entries" }).closest("[data-slot=page-acts]");
  expect(row).not.toBeNull();
  expect(document.querySelectorAll("[data-slot=page-acts]")).toHaveLength(1);

  view.rerender(app(false));
  expect(await screen.findByRole("heading", { level: 1, name: "Radar" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rebuild entries" })).toBeTruthy();
  expect(document.querySelector("[data-slot=page-acts]")).toBeNull();
});

test("a banded page offering no act hides the row it would have stood on", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  render(
    <SectionApp
      tab="radar"
      init={{ ...INIT, banded: true }}
      view={{ label: "Radar", remountOnPlace: false, render: () => <p>Entries</p> }}
    />,
  );
  expect(await screen.findByText("Entries")).toBeTruthy();
  const row = document.querySelector("[data-slot=page-acts]")!;
  expect(row.className).toContain("not-has-[:not(.contents)]:hidden");
  expect(row.querySelector("*:not(.contents)")).toBeNull();
});

test("a lane opened inside a banded page keeps its own band", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const { useSlot } = await import("@/kernel/slots");
  function Opening() {
    const lane = useSlot(<p>One report</p>, { id: "report", title: "Report", onClose: () => {} });
    return (
      <>
        {lane}
        <p>Entries</p>
      </>
    );
  }
  render(
    <SectionApp
      tab="radar"
      init={{ ...INIT, banded: true, place: { opens: ["report"] } }}
      view={{ label: "Radar", remountOnPlace: false, render: () => <Opening /> }}
    />,
  );

  expect(await screen.findByText("One report")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Report" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Close Report" })).toBeTruthy();
});

function navigations(): { posted: string[]; settled: () => Promise<void> } {
  const posted: string[] = [];
  let arrive = () => {};
  const listener = (event: MessageEvent) => {
    const message = event.data as { ufo?: string; to?: string } | null;
    if (message?.ufo === "navigate") posted.push(message.to as string);
    if (message?.ufo === "sentinel") arrive();
  };
  window.addEventListener("message", listener);
  cleanups.push(() => window.removeEventListener("message", listener));
  return {
    posted,
    settled: () =>
      new Promise<void>((resolve) => {
        arrive = resolve;
        window.postMessage({ ufo: "sentinel" }, "*");
      }),
  };
}

test("a filter inside a banded page moves the page alone; unbanded it moves the portal", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const bridge = navigations();
  const app = (banded: boolean) => (
    <SectionApp
      tab="artifacts"
      init={{ ...INIT, banded }}
      view={{
        label: "Artifacts",
        remountOnPlace: false,
        render: (place, onPlace) => (
          <>
            <button type="button" onClick={() => onPlace({ kind: "image" })}>
              Images
            </button>
            <button type="button" onClick={() => onPlace({ kind: "file" })}>
              Files
            </button>
            <p>Showing {place.kind ?? "everything"}</p>
          </>
        ),
      }}
    />
  );

  const view = render(app(true));
  fireEvent.click(await screen.findByRole("button", { name: "Images" }));
  expect(await screen.findByText("Showing image")).toBeTruthy();
  await bridge.settled();
  expect(bridge.posted).toEqual([]);

  view.rerender(app(false));
  fireEvent.click(screen.getByRole("button", { name: "Files" }));
  expect(await screen.findByText("Showing file")).toBeTruthy();
  await bridge.settled();
  expect(bridge.posted).toHaveLength(1);
  expect(bridge.posted[0]).toContain("kind=file");
});

test("a record opened inside a banded page stands in the page's own track", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const { closed, opened, useSlot } = await import("@/kernel/slots");
  const bridge = navigations();
  function Listing({ place, onPlace }: { place: Placement; onPlace: (place: Placement) => void }) {
    const opens = place.opens ?? [];
    const lane = useSlot(opens.includes("report") ? <p>One report</p> : null, {
      id: "report",
      title: "Report",
      onClose: () => onPlace({ opens: closed(opens, "report") }),
    });
    return (
      <>
        {lane}
        <button type="button" onClick={() => onPlace({ opens: opened(opens, "report") })}>
          Open report
        </button>
      </>
    );
  }
  render(
    <SectionApp
      tab="artifacts"
      init={{ ...INIT, banded: true }}
      view={{
        label: "Artifacts",
        remountOnPlace: false,
        render: (place, onPlace) => <Listing place={place} onPlace={onPlace} />,
      }}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Open report" }));
  expect(await screen.findByText("One report")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Report" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Open report" })).toBeTruthy();
  await bridge.settled();
  expect(bridge.posted).toEqual([]);
});

test("a conversation link inside a framed record rides the bridge and leaves the frame standing", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const { ObjectDetail } = await import("@/kernel/objects");
  const { MainAgentProvider } = await import("@/lib/mainAgent");
  const { Viewer } = await import("@/lib/audience");
  const { chatHash } = await import("@/lib/route");
  wire({
    "/objects/scheduled_task/nightly-deploy": () =>
      json({
        ...TASK_KIND,
        name: "nightly-deploy",
        summary: "0 9 * * * — build the nightly",
        spec: { schedule: "0 9 * * *", prompt: "build the nightly", paused: false },
        status: {
          conversation: CONVO_ID,
          next_run_at: "2026-08-27T09:00:00+00:00",
          paused: false,
        },
        links: [],
        created_at: "2026-08-01T09:00:00Z",
        updated_at: "2026-08-01T09:00:00Z",
      }),
  });
  const bridge = navigations();
  location.hash = "#/radar";
  render(
    <Viewer.Provider value={MEMBER.email}>
      <MainAgentProvider agents={[AGENT]}>
        <SectionApp
          tab="radar"
          init={{ ...INIT, banded: true }}
          view={{
            label: "Radar",
            remountOnPlace: false,
            render: () => (
              <ObjectDetail
                agentId={AGENT.id}
                kind="scheduled_task"
                name="nightly-deploy"
                onOpen={() => {}}
                onBack={() => {}}
              />
            ),
          }}
        />
      </MainAgentProvider>
    </Viewer.Provider>,
  );

  const sheet = await screen.findByRole("dialog", { name: "nightly-deploy" });
  fireEvent.click(await within(sheet).findByRole("link", { name: "Conversation" }));
  await bridge.settled();
  expect(bridge.posted).toEqual([chatHash(CONVO_ID)]);
  expect(location.hash).toBe("#/radar");
});

test("a banded page takes the lane's gutter and an unbanded one the screen's", async () => {
  vi.resetModules();
  const { SectionApp } = await import("@/apps/shell");
  const app = (banded: boolean) => (
    <SectionApp
      tab="artifacts"
      init={{ ...INIT, banded }}
      view={{ label: "Artifacts", remountOnPlace: false, render: () => <p>Entries</p> }}
    />
  );

  const view = render(app(true));
  expect(await screen.findByText("Entries")).toBeTruthy();
  const banded = document.querySelector("[data-slot=page]")!;
  expect(banded.className).toContain("p-2xl");
  expect(banded.className).not.toContain("--size-page-top");
  expect(banded.className).not.toContain("--size-page-gutter");

  view.rerender(app(false));
  const plain = document.querySelector("[data-slot=page]")!;
  expect(plain.className).toContain("px-(--size-page-gutter)");
  expect(plain.className).toContain("py-(--size-page-top)");
});

test("a page mounted through the kit alone greets the shell, reads over the bridge, and takes a frame", async () => {
  vi.resetModules();
  const { getJson, mountApp, useEffect, useState } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  const answers: Record<string, string> = {
    "/api/chats": JSON.stringify({ chats: [{ title: "Weekly report" }] }),
  };
  cleanups.push(
    shell((message) => {
      if (message.ufo !== "call") return;
      const path = (message.path as string).split("?")[0];
      if (path.endsWith("/stream")) {
        window.postMessage({ ufo: "opened", id: message.id }, "*");
        window.postMessage(
          { ufo: "frame", id: message.id, event: "message", data: '{"text":"the turn spoke"}' },
          "*",
        );
        return;
      }
      const body = answers[path];
      window.postMessage(
        body === undefined
          ? { ufo: "data", id: message.id, ok: false, error: "no answer for " + path }
          : { ufo: "data", id: message.id, ok: true, status: 200, body, refusal: null, fault: null },
        "*",
      );
    }),
  );

  function Framed({ init }: { init: AppInit }) {
    const [read, setRead] = useState("reading");
    const [heard, setHeard] = useState("silent");
    useEffect(() => {
      void getJson<{ chats: { title: string }[] }>("/api/chats").then((answer) =>
        setRead(answer.ok ? answer.payload.chats[0].title : answer.message),
      );
      const source = new EventSource("/surface/web/turns/" + TURN_ID + "/stream");
      source.onmessage = (event) => setHeard((JSON.parse(event.data) as { text: string }).text);
      return () => source.close();
    }, []);
    return (
      <>
        <p>{init.member.email}</p>
        <p>{read}</p>
        <p>{heard}</p>
      </>
    );
  }

  const root = document.createElement("div");
  document.body.append(root);
  cleanups.push(() => {
    unmountApp(root);
    root.remove();
  });
  mountApp(root, (init) => <Framed init={init} />);

  expect(root.dataset.ufoApplication).toBe("");
  expect(await screen.findByText(MEMBER.email)).toBeTruthy();
  expect(await screen.findByText("Weekly report")).toBeTruthy();
  expect(await screen.findByText("the turn spoke")).toBeTruthy();
  unmountApp(root);
  await painted();
  await painted();
});

test("a page mounted on its own opens the kit's sheet beside itself, not over nothing", async () => {
  vi.resetModules();
  const { mountApp, Sheet } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  cleanups.push(
    shell(
      (message) => {
        if (message.ufo !== "call") return;
        window.postMessage(
          {
            ufo: "data",
            id: message.id,
            ok: true,
            status: 200,
            body: JSON.stringify({ agents: [AGENT], member: MEMBER }),
            refusal: null,
            fault: null,
          },
          "*",
        );
      },
      {
        member: INIT.member,
        agentId: INIT.agentId,
        banded: false,
        place: INIT.place,
        portal: INIT.portal,
      },
    ),
  );
  const root = document.createElement("div");
  document.body.append(root);
  cleanups.push(() => {
    unmountApp(root);
    root.remove();
  });

  mountApp(root, () => (
    <>
      <p>Page body</p>
      <Sheet open title="Record" onClose={() => {}}>
        <p>Record body</p>
      </Sheet>
    </>
  ));

  const sheet = await screen.findByRole("dialog", { name: "Record" });
  const columns = root.querySelectorAll("[data-slot=resizable-panel]");
  expect(columns).toHaveLength(2);
  expect(columns[0].textContent).toContain("Page body");
  expect(columns[1].contains(sheet)).toBe(true);
});

test("a page remounted across a deploy reads its audience when its standing shell cannot carry it", async () => {
  vi.resetModules();
  const { mountApp } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  const calls: string[] = [];
  cleanups.push(
    shell(
      (message) => {
        if (message.ufo !== "call") return;
        calls.push(message.path as string);
        window.postMessage(
          {
            ufo: "data",
            id: message.id,
            ok: true,
            status: 200,
            body: JSON.stringify({ agents: [AGENT], member: MEMBER }),
            refusal: null,
            fault: null,
          },
          "*",
        );
      },
      {
        member: INIT.member,
        agentId: INIT.agentId,
        banded: false,
        place: INIT.place,
        portal: INIT.portal,
      },
    ),
  );
  const root = document.createElement("div");
  document.body.append(root);
  cleanups.push(() => {
    unmountApp(root);
    root.remove();
  });

  mountApp(root, (_init, agents) => <p>{agents[0].name}</p>);

  expect(await screen.findByText(AGENT.name)).toBeTruthy();
  expect(calls).toEqual(["/api/agents"]);
  unmountApp(root);
  await painted();
  await painted();
});

test("an application action reads its durable result and submits the exact prepared write", async () => {
  vi.resetModules();
  const { ApplicationAction } = await import("@/apps/kit");
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === "POST") {
      return new Response(JSON.stringify({ ok: true, detail: "Issue #521 assigned to alex." }), {
        status: 200,
      });
    }
    return new Response(
      JSON.stringify({ status: { result: "Issue #521 remains assigned to alex." } }),
      { status: 200 },
    );
  });
  window.fetch = fetcher as unknown as typeof fetch;
  const action = {
    label: "Assign issue 521 to Alex",
    write: {
      function: "ufoWrite" as const,
      arguments: [
        "eval_app_action",
        "assign-issue-521",
        { case: "issue-owner", action: "assign_issue", target: "521", value: "alex" },
      ] as [string, string, Record<string, unknown>],
    },
    read: {
      function: "ufoRead" as const,
      arguments: ["objects/eval_app_action/assign-issue-521"] as [string],
    },
    success_text: "Issue #521 assigned to alex.",
  };

  render(<ApplicationAction action={action} />);
  expect(await screen.findByText("Issue #521 remains assigned to alex.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: action.label }));
  expect(await screen.findByText(action.success_text)).toBeTruthy();
  expect(fetcher).toHaveBeenCalledWith(
    "/surface/web/objects/eval_app_action",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ name: action.write.arguments[1], spec: action.write.arguments[2] }),
    }),
  );
});

test("the kit publishes every route builder and the route-kind test", async () => {
  vi.resetModules();
  const kit = (await import("@/apps/kit")) as unknown as Record<string, unknown>;
  const route = (await import("@/lib/route")) as unknown as Record<string, unknown>;
  const builders = Object.keys(route).filter(
    (name) => name.endsWith("Hash") && typeof route[name] === "function",
  );

  expect(builders.length).toBeGreaterThan(0);
  for (const name of builders) expect(kit[name]).toBe(route[name]);
  expect(kit.routeIs).toBe(route.routeIs);
});

test("the pane's place reaches the page live, whole, across place messages", async () => {
  const runtime = await connected(() => {});
  const seen: (string | undefined)[] = [];
  cleanups.push(runtime.onPlaced((place) => seen.push(place.opens?.[0])));

  window.postMessage({ ufo: "place", place: { opens: ["run-1"] } }, "*");
  await vi.waitFor(() => expect(seen).toContain("run-1"));
  window.postMessage({ ufo: "place", place: {} }, "*");
  await vi.waitFor(() => expect(seen).toContain(undefined));
});

test("readies stop at the budget, and an init after the last one still mounts the page", async () => {
  vi.resetModules();
  const runtime = (await import("@/apps/runtime")) as Runtime;
  const readies: unknown[] = [];
  const count = (event: MessageEvent) => {
    if ((event.data as { ufo?: string } | null)?.ufo === "ready") readies.push(event.data);
  };
  window.addEventListener("message", count);
  cleanups.push(() => window.removeEventListener("message", count));

  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  const pending = runtime.connect();
  await vi.advanceTimersByTimeAsync(BEYOND_READY_BUDGET_MS);
  const spent = readies.length;
  await vi.advanceTimersByTimeAsync(BEYOND_READY_BUDGET_MS);
  expect(readies.length).toBe(spent);
  vi.useRealTimers();

  window.postMessage({ ufo: "init", ...INIT }, "*");
  expect((await pending).agentId).toBe(AGENT.id);
});

test("the private lifecycle closes only after terminal bridge, timer, interval, and render work", async () => {
  vi.resetModules();
  const { mountApp, useEffect, useState } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  let streamId = "";
  let setups = 0;
  let lifecycleCleanups = 0;
  let crossClearedTimeoutRan = false;
  let crossClearedIntervalRan = false;
  cleanups.push(
    shell((message) => {
      if (String(message.path).endsWith("/stream")) {
        streamId = String(message.id);
        window.postMessage({ ufo: "opened", id: streamId }, "*");
        shellSetTimeout(() => {
          window.postMessage(
            { ufo: "frame", id: streamId, event: "message", data: "streamed" },
            "*",
          );
          window.postMessage({ ufo: "end", id: streamId }, "*");
        }, 20);
        return;
      }
      shellSetTimeout(
        () =>
          window.postMessage(
            { ufo: "data", id: message.id, ok: true, status: 200, body: "loaded" },
            "*",
          ),
        20,
      );
    }),
  );

  function Fixture() {
    const [result, setResult] = useState("starting");
    useEffect(() => {
      setups += 1;
      const canceled = window.setTimeout(() => setResult("wrong"), 1);
      window.clearTimeout(canceled);
      const timeoutClearedByInterval = window.setTimeout(() => {
        crossClearedTimeoutRan = true;
      }, 1);
      window.clearInterval(timeoutClearedByInterval);
      const intervalClearedByTimeout = window.setInterval(() => {
        crossClearedIntervalRan = true;
      }, 1);
      window.clearTimeout(intervalClearedByTimeout);
      let ticks = 0;
      const interval = window.setInterval(() => {
        ticks += 1;
        if (ticks === 2) window.clearInterval(interval);
      }, 5);
      window.setTimeout(
        () =>
          window.setTimeout(async () => {
            const response = await fetch("/surface/web/data");
            setResult(await response.text());
          }, 5),
        5,
      );
      const source = new EventSource("/surface/web/run/stream");
      return () => {
        lifecycleCleanups += 1;
        window.clearInterval(interval);
        source.close();
      };
    }, []);
    return <p>{result}</p>;
  }

  const root = document.createElement("div");
  document.body.append(root);
  mountApp(root, () => <Fixture />);
  expect(await screen.findByText("loaded")).toBeTruthy();
  await painted();
  await painted();

  const descriptor = Object.getOwnPropertyDescriptor(
    window,
    "__ufoApplicationLifecycle",
  );
  expect(descriptor?.configurable).toBe(false);
  expect(descriptor?.set).toBeUndefined();
  const symbolFor = Symbol.for;
  Symbol.for = ((key: string) => Symbol(key)) as typeof Symbol.for;
  expect(lifecycle()).toBe(descriptor?.get?.());
  Symbol.for = symbolFor;
  expect(Object.isFrozen(lifecycle())).toBe(true);
  const immutableSnapshot = lifecycle().snapshot();
  expect(Object.isFrozen(immutableSnapshot)).toBe(true);
  expect(Object.isFrozen(immutableSnapshot.blocking)).toBe(true);
  const currentRequestAnimationFrame = window.requestAnimationFrame;
  let replacedAnimationFrameCalls = 0;
  window.requestAnimationFrame = () => {
    replacedAnimationFrameCalls += 1;
    return 1;
  };
  await lifecycle().afterPaint();
  window.requestAnimationFrame = currentRequestAnimationFrame;
  expect(replacedAnimationFrameCalls).toBe(0);
  expect(() => Object.assign(lifecycle(), { snapshot: () => ({}) })).toThrow();
  expect(() =>
    Object.assign(window, {
      __ufoApplicationLifecycle: { snapshot: () => ({ state: "spoofed" }) },
    }),
  ).toThrow();
  expect(() =>
    Object.defineProperty(window, "__ufoApplicationLifecycle", {
      value: { snapshot: () => ({ state: "spoofed" }) },
    }),
  ).toThrow();
  expect(streamId).not.toBe("");
  expect(setups).toBe(2);
  expect(lifecycleCleanups).toBe(1);
  await vi.waitFor(() => expect(lifecycle().snapshot().blockingWork).toBe(0));
  expect(lifecycle().snapshot()).toEqual(
    expect.objectContaining({ mounted: true, state: "idle", blockingWork: 0 }),
  );
  expect(root.dataset.ufoApplicationState).toBe("idle");

  const before = lifecycle().snapshot().revision;
  const epoch = lifecycle().beginObservation();
  expect(() => lifecycle().beginObservation()).toThrow(
    "application observation epoch is already active",
  );
  window.setTimeout(() => root.setAttribute("data-finished", "true"), 5);
  await lifecycle().endObservation(epoch);
  await new Promise((resolve) => shellSetTimeout(resolve, 10));
  await painted();
  expect(root.dataset.finished).toBe("true");
  expect(lifecycle().snapshot().revision).toBeGreaterThan(before);
  expect(lifecycle().snapshot().blockingWork).toBe(0);
  expect(crossClearedTimeoutRan).toBe(false);
  expect(crossClearedIntervalRan).toBe(false);

  const otherRoot = document.createElement("div");
  const blockingTimeout = window.setTimeout(() => undefined, 5000);
  const heartbeat = window.setInterval(() => undefined, 30_000);
  const active = lifecycle().snapshot();
  expect(active.blocking.timeout).toBe(1);
  expect(active.blocking.interval).toBe(0);
  expect(() => applicationLifecycle.beginApplicationMount(root)).toThrow(
    "application is already mounted",
  );
  expect(() => applicationLifecycle.beginApplicationMount(otherRoot)).toThrow(
    "application is already mounted",
  );
  const afterRejectedMount = lifecycle().snapshot();
  expect(afterRejectedMount.generation).toBe(active.generation);
  expect(afterRejectedMount.blocking.timeout).toBe(1);
  expect(afterRejectedMount.blocking.interval).toBe(0);
  window.clearTimeout(blockingTimeout);
  window.clearInterval(heartbeat);
  await painted();

  let staleTimerRan = false;
  let staleIntervalTicks = 0;
  const unmountedGeneration = lifecycle().snapshot().generation;
  window.setTimeout(() => {
    staleTimerRan = true;
    root.dataset.stale = "true";
  }, 10);
  window.setInterval(() => {
    staleIntervalTicks += 1;
    root.dataset.staleInterval = "true";
  }, 5);
  unmountApp(root);
  expect(lifecycleCleanups).toBe(2);
  expect(lifecycle().snapshot()).toEqual(
    expect.objectContaining({ mounted: false, state: "unmounted", blockingWork: 0 }),
  );
  window.setTimeout(() => {
    root.textContent = "between mounts";
  }, 10);
  mountApp(root, () => <p>remounted</p>);
  expect(await screen.findByText("remounted")).toBeTruthy();
  await new Promise((resolve) => shellSetTimeout(resolve, 20));
  await painted();
  expect(staleTimerRan).toBe(false);
  expect(staleIntervalTicks).toBe(0);
  expect(root.dataset.stale).toBeUndefined();
  expect(root.dataset.staleInterval).toBeUndefined();
  expect(root.textContent).toBe("remounted");
  expect(lifecycle().snapshot()).toEqual(
    expect.objectContaining({
      generation: unmountedGeneration + 1,
      mounted: true,
      state: "idle",
      blockingWork: 0,
    }),
  );
  unmountApp(root);
  let lateTimerRan = false;
  window.setTimeout(() => {
    lateTimerRan = true;
  }, 1);
  await new Promise((resolve) => shellSetTimeout(resolve, 5));
  expect(lateTimerRan).toBe(true);
  expect(lifecycle().snapshot().blockingWork).toBe(0);
  root.remove();
});

test("a polling page reaches idle between ticks, and a tick's own work still blocks", async () => {
  vi.resetModules();
  const { mountApp, useEffect, useState } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  cleanups.push(shell(() => {}));
  await vi.waitFor(() => expect(lifecycle().snapshot().blockingWork).toBe(0));

  function Polling() {
    const [text, setText] = useState("polling");
    useEffect(() => {
      const poll = window.setInterval(() => setText("polled"), 30_000);
      return () => window.clearInterval(poll);
    }, []);
    return <p>{text}</p>;
  }

  const root = document.createElement("div");
  document.body.append(root);
  mountApp(root, () => <Polling />);
  expect(await screen.findByText("polling")).toBeTruthy();
  await vi.waitFor(async () => {
    const before = lifecycle().snapshot();
    await lifecycle().afterPaint();
    const after = lifecycle().snapshot();
    expect(before).toEqual(
      expect.objectContaining({ mounted: true, state: "idle", blockingWork: 0 }),
    );
    expect(after).toEqual(
      expect.objectContaining({ mounted: true, state: "idle", blockingWork: 0 }),
    );
    expect(after.revision).toBe(before.revision);
  });

  const heldInsideTicks: number[] = [];
  const ticking = window.setInterval(() => {
    heldInsideTicks.push(lifecycle().snapshot().blocking.interval);
  }, 5);
  await vi.waitFor(() => expect(heldInsideTicks.length).toBeGreaterThan(1));
  window.clearInterval(ticking);
  expect(heldInsideTicks.every((held) => held === 1)).toBe(true);
  expect(lifecycle().snapshot().blocking.interval).toBe(0);
  await vi.waitFor(() => expect(lifecycle().snapshot().blockingWork).toBe(0));
  expect(lifecycle().snapshot().state).toBe("idle");

  let finishClearedTick: (() => void) | undefined;
  let cleared = 0;
  cleared = window.setInterval(() => {
    window.clearInterval(cleared);
    return new Promise<void>((resolve) => (finishClearedTick = resolve));
  }, 5);
  await vi.waitFor(() => expect(finishClearedTick).toBeTypeOf("function"));
  expect(lifecycle().snapshot().blocking.interval).toBe(1);
  finishClearedTick?.();
  await painted();
  await vi.waitFor(() => expect(lifecycle().snapshot().blocking.interval).toBe(0));

  let finishUnmountedTick: (() => void) | undefined;
  window.setInterval(
    () => new Promise<void>((resolve) => (finishUnmountedTick = resolve)),
    50,
  );
  await vi.waitFor(() => expect(finishUnmountedTick).toBeTypeOf("function"));
  unmountApp(root);
  expect(lifecycle().snapshot().blocking.interval).toBe(1);
  expect(() => mountApp(root, () => <p>too soon</p>)).toThrow(
    "application work is still settling",
  );
  finishUnmountedTick?.();
  await painted();
  await vi.waitFor(() => expect(lifecycle().snapshot().blockingWork).toBe(0));
  mountApp(root, () => <p>ready again</p>);
  expect(await screen.findByText("ready again")).toBeTruthy();
  unmountApp(root);
  root.remove();
});

test("a recursive timeout reaches idle between callbacks and blocks while a callback runs", async () => {
  vi.resetModules();
  const { mountApp, useEffect, useState } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  cleanups.push(shell(() => {}));
  await vi.waitFor(() => expect(lifecycle().snapshot().blockingWork).toBe(0));

  const heldInsideCallbacks: number[] = [];

  function Polling() {
    const [text, setText] = useState("waiting");
    useEffect(() => {
      let timer = 0;
      const poll = () => {
        heldInsideCallbacks.push(lifecycle().snapshot().blocking.timeout);
        setText("polled");
        timer = window.setTimeout(poll, 30_000);
      };
      timer = window.setTimeout(poll, 5);
      return () => window.clearTimeout(timer);
    }, []);
    return <p>{text}</p>;
  }

  const root = document.createElement("div");
  document.body.append(root);
  mountApp(root, () => <Polling />);
  expect(await screen.findByText("polled")).toBeTruthy();
  expect(heldInsideCallbacks).toEqual([1]);
  await vi.waitFor(async () => {
    const before = lifecycle().snapshot();
    await lifecycle().afterPaint();
    const after = lifecycle().snapshot();
    expect(before).toEqual(
      expect.objectContaining({ mounted: true, state: "idle", blockingWork: 0 }),
    );
    expect(after).toEqual(
      expect.objectContaining({ mounted: true, state: "idle", blockingWork: 0 }),
    );
    expect(after.revision).toBe(before.revision);
  });

  unmountApp(root);
  root.remove();
});

test("unmount blocks remount until async timer and bridge work settle", async () => {
  const { mountApp, useEffect } = await import("@/apps/kit");
  const { unmountApp } = await import("@/apps/shell");
  const calls: Record<string, unknown>[] = [];
  cleanups.push(shell((message) => calls.push(message)));
  const finishTimers: (() => void)[] = [];

  function Holding() {
    useEffect(() => {
      window.setTimeout(
        () =>
          new Promise<void>((resolve) => {
            finishTimers.push(resolve);
          }),
        0,
      );
    }, []);
    return <p>holding</p>;
  }

  const root = document.createElement("div");
  document.body.append(root);
  mountApp(root, () => <Holding />);
  expect(await screen.findByText("holding")).toBeTruthy();
  await vi.waitFor(() => expect(finishTimers).toHaveLength(2));
  const unary = fetch("/surface/web/pending").then((response) => response.text());
  const stream = new EventSource("/surface/web/pending-stream");
  await vi.waitFor(() => expect(calls.filter((call) => call.ufo === "call")).toHaveLength(2));

  unmountApp(root);
  expect(() => mountApp(root, () => <p>too soon</p>)).toThrow(
    "application work is still settling",
  );
  for (const finish of finishTimers) finish();
  const unaryCall = calls.find((call) => call.path === "/pending");
  const streamCall = calls.find((call) => call.path === "/pending-stream");
  window.postMessage(
    { ufo: "data", id: unaryCall?.id, ok: true, status: 200, body: "settled" },
    "*",
  );
  window.postMessage({ ufo: "end", id: streamCall?.id }, "*");
  expect(await unary).toBe("settled");
  await painted();
  await painted();
  await vi.waitFor(() => expect(lifecycle().snapshot().blockingWork).toBe(0));
  expect(lifecycle().snapshot()).toEqual(
    expect.objectContaining({ mounted: false, state: "unmounted", blockingWork: 0 }),
  );

  mountApp(root, () => <p>ready again</p>);
  expect(await screen.findByText("ready again")).toBeTruthy();
  expect(lifecycle().snapshot().generation).toBeGreaterThan(1);
  stream.close();
  unmountApp(root);
  root.remove();
});
