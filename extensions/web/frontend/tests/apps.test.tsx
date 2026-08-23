import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { objectIndex, wire, AGENT, MEMBER, TASK_KIND } from "./harness";

/** The app page's side of the bridge, tested against a fake shell on this same window: jsdom's
 *  `window.top` is the window itself, so the runtime's posts land on our own listener and our
 *  replies land on the runtime's. Each test imports the module fresh — the correlation maps and
 *  the held init are module state. */

type Runtime = typeof import("@/apps/runtime");

const INIT = {
  member: { email: MEMBER.email, admin: true },
  agentId: AGENT.id,
  open: null,
  portal: location.origin,
};

function shell(onCall: (message: Record<string, unknown>) => void): () => void {
  const listener = (event: MessageEvent) => {
    const message = event.data as { ufo?: string } | null;
    if (!message || typeof message.ufo !== "string") return;
    if (message.ufo === "ready") {
      window.postMessage({ ufo: "init", ...INIT }, "*");
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

const cleanups: (() => void)[] = [];
const nativeFetch = window.fetch;

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  for (const cleanup of cleanups.splice(0)) cleanup();
  window.fetch = nativeFetch;
});

test("a portal fetch rides the bridge and comes back as the surface's own response", async () => {
  const runtime = await connected((message) => {
    expect(message.method).toBe("GET");
    expect(message.path).toBe("/api/chats");
    window.postMessage(
      {
        ufo: "data",
        id: message.id,
        ok: true,
        status: 200,
        body: JSON.stringify({ chats: [] }),
        refusal: null,
        fault: null,
      },
      "*",
    );
  });
  runtime.installShims();
  const res = await fetch("/surface/web/api/chats");
  expect(res.status).toBe(200);
  expect(await res.json()).toEqual({ chats: [] });
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
            agentId: AGENT.id,
            open: null,
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

test("an app's TSX page compiles in the browser and runs against the kit global", async () => {
  vi.resetModules();
  const { compile } = await import("@/apps/kit");
  const spoken: string[] = [];
  (window as { UfoAppKit?: unknown }).UfoAppKit = { say: (word: string) => spoken.push(word) };
  const source = [
    "const { say } = UfoAppKit;",
    "type Held = { word: string };",
    "const held: Held = { word: \"compiled\" };",
    "function Page({ word }: Held) { return <b>{word}</b>; }",
    "say(held.word + \":\" + typeof Page);",
  ].join("\n");
  new Function(compile(source))();
  expect(spoken).toEqual(["compiled:function"]);
  delete (window as { UfoAppKit?: unknown }).UfoAppKit;
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
