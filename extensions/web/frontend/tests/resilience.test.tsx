import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { chatHash } from "@/lib/route";
import { setReattachTimer } from "@/lib/turnStream";

import { AGENT, ARRIVAL_ID, CHAT_ROW, chatsOnWire, CONVO_ID, json, MEMBER, saying, SECOND, StreamFake, TURN_ID, useStreamFake, wire } from "./harness";

const OTHER_ID = "66666666-6666-4666-8666-666666666666";
const TODAY = new Date().toISOString();
const MINE = { ...CHAT_ROW, last_at: TODAY };
const OTHER_ROW = { ...MINE, conversation_id: OTHER_ID, title: "The other thread" };
const RAIL = { chats: [MINE, OTHER_ROW] };
const LATER_TURN = "77777777-7777-4777-8777-777777777777";
const START_MS = 1_700_000_000_000;
// The clock re-reads itself once a second, so a fresh reading lands a tick after the reattach.
const PAST_ONE_TICK = { timeout: 3_000 };

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

function follow(hash: string): void {
  location.hash = hash;
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function fatal(stream: StreamFake) {
  stream.readyState = StreamFake.CLOSED;
  stream.fail();
}

async function streaming(routes: Record<string, () => Response> = {}) {
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
    ...routes,
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "go");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

test("a dropped stream reattaches and the replay rebuilds the reply without duplication", async () => {
  const pending: (() => void)[] = [];
  setReattachTimer((fn) => {
    pending.push(fn);
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  first.emit("message", { text: "one " });
  await screen.findByText(saying("one"));

  fatal(first);
  expect(await screen.findByText("Reconnecting…")).toBeTruthy();
  expect(pending.length).toBe(1);

  pending[0]();
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  const second = StreamFake.last();
  expect(await screen.findByText(saying("one"))).toBeTruthy();
  second.emit("open", {});
  second.emit("message", { text: "one " });
  second.emit("message", { text: "two" });
  expect(await screen.findByText(saying("one two"))).toBeTruthy();
  expect(screen.queryByText("one one two")).toBeNull();

  second.emit("terminal", { status: "done", model: "opus", tokens: 3, cost_micro_usd: 0 });
  expect(await screen.findByText("opus · 3 tok · $0.00")).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false),
  );
});

test("a reattach the backoff still holds opens nothing behind the source that replaced it", async () => {
  const pending = new Map<number, () => void>();
  let handed = 0;
  setReattachTimer((fn) => {
    handed += 1;
    pending.set(handed, fn);
    return handed as unknown as ReturnType<typeof setTimeout>;
  });
  const native = clearTimeout;
  vi.stubGlobal("clearTimeout", (timer: unknown) => {
    if (typeof timer === "number" && pending.delete(timer)) return;
    native(timer as Parameters<typeof clearTimeout>[0]);
  });
  const first = await streaming({
    "/chat": () => json({ turn_id: LATER_TURN, conversation_id: CONVO_ID, title: "go" }),
  });
  fatal(first);
  await screen.findByText("Reconnecting…");
  expect(pending.size).toBe(1);

  // The reattach it overtook has to be cancelled, not merely forgotten: firing later, it would open a
  // source behind the live one.
  await userEvent.type(screen.getByLabelText("Ask UFO"), "again");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));

  expect(pending.size).toBe(0);
  for (const fire of [...pending.values()]) fire();
  expect(StreamFake.opened.length).toBe(2);
  expect(StreamFake.last().url).toBe("/surface/web/turns/" + LATER_TURN + "/stream");
});

test("a send during the backoff rebuilds the reply from the replay it reattached to", async () => {
  const pending: (() => void)[] = [];
  setReattachTimer((fn) => {
    pending.push(fn);
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming({
    "/chat": () =>
      json({
        turn_id: TURN_ID,
        conversation_id: CONVO_ID,
        title: "go",
        opened_run: false,
        arrival_id: ARRIVAL_ID,
      }),
  });
  first.emit("message", { text: "one " });
  await screen.findByText(saying("one"));
  fatal(first);
  await screen.findByText("Reconnecting…");

  await userEvent.type(screen.getByLabelText("Ask UFO"), "and again");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));

  const second = StreamFake.last();
  second.emit("open", {});
  second.emit("message", { text: "one " });
  second.emit("message", { text: "two" });
  expect(await screen.findByText(saying("one two"))).toBeTruthy();
  expect(screen.queryByText("one one two")).toBeNull();
});

test("a reattached turn counts from the turn's start, not from the reattach", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  const pending: (() => void)[] = [];
  setReattachTimer((fn) => {
    pending.push(fn);
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  first.emit("message", { text: "one " });
  await screen.findByText(saying("one"));

  clock.mockReturnValue(START_MS + 30_000);
  fatal(first);
  await screen.findByText("Reconnecting…");
  pending[0]();
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  const second = StreamFake.last();
  second.emit("open", {});
  second.emit("activity", { text: "Reading the changelog." });
  second.emit("message", { text: "one two" });
  await screen.findByText(saying("one two"));
  expect(await screen.findByText("30s", undefined, PAST_ONE_TICK)).toBeTruthy();

  clock.mockReturnValue(START_MS + 102_000);
  second.emit("terminal", { status: "done", model: "opus", tokens: 3, cost_micro_usd: 0 });
  expect(await screen.findByText("Completed 1 step in 1m 42s")).toBeTruthy();
  clock.mockRestore();
});

test("a reattach on returning to the tab keeps the clock the turn started", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  setReattachTimer(() => 0 as unknown as ReturnType<typeof setTimeout>);
  const first = await streaming();
  fatal(first);
  await screen.findByText("Reconnecting…");

  clock.mockReturnValue(START_MS + 45_000);
  window.dispatchEvent(new Event("focus"));
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  StreamFake.last().emit("open", {});
  StreamFake.last().emit("message", { text: "rejoined" });
  await screen.findByText(saying("rejoined"));
  expect(await screen.findByText("45s", undefined, PAST_ONE_TICK)).toBeTruthy();
  clock.mockRestore();
});

test("a send that rejoins the running turn keeps the clock that turn started", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  setReattachTimer(() => 0 as unknown as ReturnType<typeof setTimeout>);
  const first = await streaming({
    "/chat": () =>
      json({
        turn_id: TURN_ID,
        conversation_id: CONVO_ID,
        title: "go",
        opened_run: false,
        arrival_id: ARRIVAL_ID,
      }),
  });
  first.emit("message", { text: "one " });
  await screen.findByText(saying("one"));

  clock.mockReturnValue(START_MS + 20_000);
  fatal(first);
  await screen.findByText("Reconnecting…");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "and again");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));

  const second = StreamFake.last();
  second.emit("open", {});
  second.emit("message", { text: "one two" });
  await screen.findByText(saying("one two"));
  expect(await screen.findByText("20s", undefined, PAST_ONE_TICK)).toBeTruthy();
  clock.mockRestore();
});

async function reloadedInto(payload: Record<string, unknown>) {
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => json({ messages: [{ role: "user", text: "Review PR 1268." }], ...payload }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  return StreamFake.last();
}

test("a page loaded into a running turn counts from the turn's start, not from the load", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  const stream = await reloadedInto({
    turn: TURN_ID,
    turn_started_at: new Date(START_MS - 480_000).toISOString(),
  });
  stream.emit("activity", { text: "Reading the diff." });
  stream.emit("message", { text: "still going" });
  await screen.findByText(saying("still going"));
  expect(await screen.findByText("8m 0s", undefined, PAST_ONE_TICK)).toBeTruthy();

  clock.mockReturnValue(START_MS + 60_000);
  stream.emit("terminal", { status: "done", model: "opus", tokens: 3, cost_micro_usd: 0 });
  expect(await screen.findByText("Completed 1 step in 9m 0s")).toBeTruthy();
  clock.mockRestore();
});

test("a transcript that states no start counts the turn from the load", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(START_MS);
  const stream = await reloadedInto({ turn: TURN_ID });
  stream.emit("message", { text: "still going" });
  await screen.findByText(saying("still going"));
  expect(await screen.findByText("0s", undefined, PAST_ONE_TICK)).toBeTruthy();
  clock.mockRestore();
});

test("a native retry shows a quiet reconnecting state and the stream carries on", async () => {
  const stream = await streaming();
  stream.emit("message", { text: "partial" });
  stream.fail();
  expect(await screen.findByText("Reconnecting…")).toBeTruthy();
  expect(screen.queryByText("Connection lost — reload to see the reply.")).toBeNull();
  expect(StreamFake.opened.length).toBe(1);

  stream.emit("open", {});
  await waitFor(() => expect(screen.queryByText("Reconnecting…")).toBeNull());
  stream.emit("message", { text: " resumed" });
  expect(await screen.findByText(saying("partial resumed"))).toBeTruthy();
});

test("persistent fatal closes give up with the loss stated once", async () => {
  setReattachTimer((fn) => {
    fn();
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  first.emit("message", { text: "kept" });
  for (let round = 0; round < 7; round += 1) fatal(StreamFake.last());

  expect(await screen.findByText("Connection lost — reload to see the reply.")).toBeTruthy();
  expect(screen.getByText("kept")).toBeTruthy();
  expect(StreamFake.opened.length).toBe(7);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false),
  );
});

test("returning to a tab with a dead stream reattaches without waiting out the backoff", async () => {
  const pending: (() => void)[] = [];
  setReattachTimer((fn) => {
    pending.push(fn);
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  fatal(first);
  await screen.findByText("Reconnecting…");

  window.dispatchEvent(new Event("focus"));
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  StreamFake.last().emit("open", {});
  StreamFake.last().emit("message", { text: "rejoined" });
  expect(await screen.findByText(saying("rejoined"))).toBeTruthy();
});

test("returning to an idle tab refetches the transcript", async () => {
  let serves = 0;
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => {
      serves += 1;
      return json({
        messages: serves === 1 ? [] : [{ role: "assistant", text: "fresh from the server" }],
      });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");

  window.dispatchEvent(new Event("focus"));
  expect(await screen.findByText("fresh from the server")).toBeTruthy();
  expect(serves).toBe(2);
});

test("a transcript that never loads says so in the pane, not as an empty conversation", async () => {
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => new Response("nope", { status: 503 }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("The conversation did not load.")).toBeTruthy();
  expect(screen.getByText("Error 503 — reload to retry.")).toBeTruthy();
  expect(screen.queryByText("No messages in this conversation yet.")).toBeNull();
  expect(document.querySelector("[data-slot=toast]")).toBeNull();
});

test("a re-read that fails states so over the conversation it already holds", async () => {
  let serves = 0;
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => {
      serves += 1;
      if (serves === 1) return json({ messages: [{ role: "assistant", text: "still here" }] });
      return new Response("nope", { status: 503 });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("still here");

  window.dispatchEvent(new Event("focus"));
  const toast = (await screen.findByText(/did not load\./)).closest("[data-slot=toast]") as HTMLElement;
  expect(toast.textContent).toContain("The conversation did not load.");
  expect(screen.getByText("still here")).toBeTruthy();
});

test("a draft survives leaving the chat and is cleared by sending", async () => {
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID, conversation_id: CONVO_ID, title: "go" }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");

  await userEvent.type(screen.getByLabelText("Ask UFO"), "half a thought");
  window.dispatchEvent(new Event("pagehide"));
  follow(chatHash(OTHER_ID));
  await screen.findByText("No messages in this conversation yet.");
  follow(chatHash(CONVO_ID));

  const draft = () => (screen.getByLabelText("Ask UFO") as HTMLInputElement).value;
  await waitFor(() => expect(draft()).toBe("half a thought"));

  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  window.dispatchEvent(new Event("pagehide"));
  follow(chatHash(OTHER_ID));
  await waitFor(() => expect(draft()).toBe(""));
  follow(chatHash(CONVO_ID));
  await waitFor(() => expect(draft()).toBe(""));
});

test("the terminal's full text overrides a gap-truncated replay", async () => {
  const pending: (() => void)[] = [];
  setReattachTimer((fn) => {
    pending.push(fn);
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  first.emit("message", { text: "one " });
  fatal(first);
  pending[0]();
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  const second = StreamFake.last();
  second.emit("open", {});
  second.emit("message", { text: "two" });
  await screen.findByText(saying("two"));
  second.emit("terminal", {
    status: "done",
    text: "one two final",
    model: "opus",
    tokens: 3,
    cost_micro_usd: 0,
  });
  expect(await screen.findByText("one two final")).toBeTruthy();
  expect(screen.queryByText(/^two$/)).toBeNull();
});

test("a native blip on a reattached source never wipes the rebuilt text", async () => {
  const pending: (() => void)[] = [];
  setReattachTimer((fn) => {
    pending.push(fn);
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  fatal(first);
  pending[0]();
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  const second = StreamFake.last();
  second.emit("open", {});
  second.emit("message", { text: "rebuilt" });
  await screen.findByText(saying("rebuilt"));

  second.fail();
  second.emit("open", {});
  second.emit("message", { text: " intact" });
  expect(await screen.findByText(saying("rebuilt intact"))).toBeTruthy();
});

test("a transcript fetched before a send never erases the exchange", async () => {
  let release: (value: Response) => void = () => {};
  let serves = 0;
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => {
      serves += 1;
      if (serves === 1) return json({ messages: [] });
      return new Promise<Response>((resolve) => (release = resolve));
    },
    "/chat": () => json({ turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");

  window.dispatchEvent(new Event("focus"));
  await waitFor(() => expect(serves).toBe(2));
  await userEvent.type(screen.getByLabelText("Ask UFO"), "just sent");
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("just sent");
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));
  StreamFake.last().emit("terminal", {
    status: "done",
    text: "settled reply",
    model: "opus",
    tokens: 1,
    cost_micro_usd: 0,
  });
  await screen.findByText("settled reply");

  release(json({ messages: [] }));
  await waitFor(() => expect(serves).toBe(2));
  expect(screen.getByText("just sent")).toBeTruthy();
  expect(screen.getByText("settled reply")).toBeTruthy();
});

test("a focus during a healthy stream opens nothing new", async () => {
  await streaming();
  window.dispatchEvent(new Event("focus"));
  expect(StreamFake.opened.length).toBe(1);
});

test("reattach delays climb the declared ladder", async () => {
  const delays: number[] = [];
  setReattachTimer((fn, ms) => {
    delays.push(ms);
    fn();
    return 0 as unknown as ReturnType<typeof setTimeout>;
  });
  const first = await streaming();
  fatal(first);
  fatal(StreamFake.last());
  fatal(StreamFake.last());
  expect(delays).toEqual([1_000, 2_000, 4_000]);
});

test("a failed idle refetch keeps what the member already sees", async () => {
  let serves = 0;
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => {
      serves += 1;
      if (serves === 1) return json({ messages: [{ role: "assistant", text: "kept history" }] });
      return new Response("down", { status: 500 });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("kept history");

  window.dispatchEvent(new Event("focus"));
  await waitFor(() => expect(serves).toBe(2));
  expect(screen.getByText("kept history")).toBeTruthy();
});

test("returning visibility alone refetches an idle transcript", async () => {
  let serves = 0;
  wire({
    ...chatsOnWire(RAIL.chats),
    "/transcript": () => {
      serves += 1;
      return json({
        messages: serves === 1 ? [] : [{ role: "assistant", text: "visible again" }],
      });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");

  document.dispatchEvent(new Event("visibilitychange"));
  expect(await screen.findByText("visible again")).toBeTruthy();
});

test("a draft never crosses members on a shared browser", async () => {
  wire({ ...chatsOnWire(RAIL.chats), "/transcript": () => json({ messages: [] }) });
  const first = render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");
  await userEvent.type(screen.getByLabelText("Ask UFO"), "private thought");
  window.dispatchEvent(new Event("pagehide"));
  first.unmount();

  const other = render(
    <App agents={[AGENT, SECOND]} member={{ ...MEMBER, id: "m2", email: "other@example.com" }} onAgents={() => {}} />,
  );
  await screen.findByText("No messages in this conversation yet.");
  expect((screen.getByLabelText("Ask UFO") as HTMLInputElement).value).toBe("");
  other.unmount();

  render(<App agents={[AGENT, SECOND]} member={MEMBER} onAgents={() => {}} />);
  await screen.findByText("No messages in this conversation yet.");
  expect((screen.getByLabelText("Ask UFO") as HTMLInputElement).value).toBe(
    "private thought",
  );
});
