import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import type { AppInit } from "@/apps/kit";

import { objectIndex, wire, AGENT, MEMBER, TASK_KIND, TURN_ID } from "./harness";

/** The app page's side of the bridge, tested against a fake shell on this same window: jsdom's
 *  `window.top` is the window itself, so the runtime's posts land on our own listener and our
 *  replies land on the runtime's. Each test imports the module fresh — the correlation maps and
 *  the held init are module state. A page reaches the shell through the imported kit and nothing
 *  else, so no test here defines a global for it to find. */

type Runtime = typeof import("@/apps/runtime");

const INIT = {
  member: { email: MEMBER.email, admin: true },
  agents: [AGENT],
  agentId: AGENT.id,
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
          tab="tasks"
          init={{
            member: { email: MEMBER.email, admin: true },
            agents: [AGENT],
            agentId: AGENT.id,
            place: {},
            portal: location.origin,
          }}
          view={{
            label: "Tasks",
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
  expect(await screen.findByRole("heading", { name: "Tasks" })).toBeTruthy();
});

test("a page mounted through the kit alone greets the shell, reads over the bridge, and takes a frame", async () => {
  vi.resetModules();
  const { getJson, mountApp, useEffect, useState } = await import("@/apps/kit");
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
  cleanups.push(() => root.remove());
  mountApp(root, (init) => <Framed init={init} />);

  expect(root.dataset.ufoApplication).toBe("");
  expect(await screen.findByText(MEMBER.email)).toBeTruthy();
  expect(await screen.findByText("Weekly report")).toBeTruthy();
  expect(await screen.findByText("the turn spoke")).toBeTruthy();
});

test("a page remounted across a deploy reads its audience when its standing shell cannot carry it", async () => {
  vi.resetModules();
  const { mountApp } = await import("@/apps/kit");
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
        place: INIT.place,
        portal: INIT.portal,
      },
    ),
  );
  const root = document.createElement("div");
  document.body.append(root);
  cleanups.push(() => root.remove());

  mountApp(root, (_init, agents) => <p>{agents[0].name}</p>);

  expect(await screen.findByText(AGENT.name)).toBeTruthy();
  expect(calls).toEqual(["/api/agents"]);
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

/** A page spells no address by hand: every builder the route table declares stands on the kit, so a
 *  builder that is renamed breaks the page at `tsc` rather than inside a frame at runtime. */
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

  vi.useFakeTimers();
  const pending = runtime.connect();
  await vi.advanceTimersByTimeAsync(BEYOND_READY_BUDGET_MS);
  const spent = readies.length;
  await vi.advanceTimersByTimeAsync(BEYOND_READY_BUDGET_MS);
  expect(readies.length).toBe(spent);
  vi.useRealTimers();

  window.postMessage({ ufo: "init", ...INIT }, "*");
  expect((await pending).agentId).toBe(AGENT.id);
});
