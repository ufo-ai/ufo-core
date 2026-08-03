import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { setReattachTimer } from "@/lib/turnStream";

import { AGENT, MEMBER, SECOND, StreamFake, TURN_ID, json, useStreamFake, wire } from "./harness";

beforeEach(() => {
  location.hash = "#/agents/" + AGENT.id + "/chat";
  useStreamFake();
});

function fatal(stream: StreamFake) {
  stream.readyState = StreamFake.CLOSED;
  stream.fail();
}

async function streaming(routes: Record<string, () => Response> = {}) {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID }),
    ...routes,
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "go");
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
  await screen.findByText("one");

  fatal(first);
  expect(await screen.findByText("Reconnecting…")).toBeTruthy();
  expect(pending.length).toBe(1);

  pending[0]();
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  const second = StreamFake.last();
  expect(await screen.findByText("one")).toBeTruthy();
  second.emit("open", {});
  second.emit("message", { text: "one " });
  second.emit("message", { text: "two" });
  expect(await screen.findByText("one two")).toBeTruthy();
  expect(screen.queryByText("one one two")).toBeNull();

  second.emit("terminal", { status: "done", model: "opus", tokens: 3, cost_micro_usd: 0 });
  expect(await screen.findByText("opus · 3 tok · $0.00")).toBeTruthy();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(false),
  );
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
  expect(await screen.findByText("partial resumed")).toBeTruthy();
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
  expect(await screen.findByText("rejoined")).toBeTruthy();
});

test("returning to an idle tab refetches the transcript", async () => {
  let serves = 0;
  wire({
    "/transcript": () => {
      serves += 1;
      return json({
        messages: serves === 1 ? [] : [{ role: "assistant", text: "fresh from the server" }],
      });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");

  window.dispatchEvent(new Event("focus"));
  expect(await screen.findByText("fresh from the server")).toBeTruthy();
  expect(serves).toBe(2);
});

test("a draft survives leaving the chat and is cleared by sending", async () => {
  wire({
    "/transcript": () => json({ messages: [] }),
    "/chat": () => json({ turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");

  await userEvent.type(screen.getByLabelText("Message the agent"), "half a thought");
  window.dispatchEvent(new Event("pagehide"));
  await userEvent.click(screen.getByRole("button", { name: /second/ }));
  await screen.findByText("No conversation with second yet.");
  await userEvent.click(screen.getByRole("button", { name: /assistant/ }));

  const input = screen.getByLabelText("Message the agent") as HTMLInputElement;
  expect(input.value).toBe("half a thought");

  await userEvent.click(screen.getByRole("button", { name: "Send" }));
  window.dispatchEvent(new Event("pagehide"));
  await userEvent.click(screen.getByRole("button", { name: /second/ }));
  await userEvent.click(screen.getByRole("button", { name: /assistant/ }));
  expect((screen.getByLabelText("Message the agent") as HTMLInputElement).value).toBe("");
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
  await screen.findByText("two");
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
  await screen.findByText("rebuilt");

  second.fail();
  second.emit("open", {});
  second.emit("message", { text: " intact" });
  expect(await screen.findByText("rebuilt intact")).toBeTruthy();
});

test("a transcript fetched before a send never erases the exchange", async () => {
  let release: (value: Response) => void = () => {};
  let serves = 0;
  wire({
    "/transcript": () => {
      serves += 1;
      if (serves === 1) return json({ messages: [] });
      return new Promise<Response>((resolve) => (release = resolve));
    },
    "/chat": () => json({ turn_id: TURN_ID }),
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");

  window.dispatchEvent(new Event("focus"));
  await waitFor(() => expect(serves).toBe(2));
  await userEvent.type(screen.getByLabelText("Message the agent"), "just sent");
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
    "/transcript": () => {
      serves += 1;
      if (serves === 1) return json({ messages: [{ role: "assistant", text: "kept history" }] });
      return new Response("down", { status: 500 });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("kept history");

  window.dispatchEvent(new Event("focus"));
  await waitFor(() => expect(serves).toBe(2));
  expect(screen.getByText("kept history")).toBeTruthy();
});

test("returning visibility alone refetches an idle transcript", async () => {
  let serves = 0;
  wire({
    "/transcript": () => {
      serves += 1;
      return json({
        messages: serves === 1 ? [] : [{ role: "assistant", text: "visible again" }],
      });
    },
  });
  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");

  document.dispatchEvent(new Event("visibilitychange"));
  expect(await screen.findByText("visible again")).toBeTruthy();
});

test("a draft never crosses members on a shared browser", async () => {
  wire({ "/transcript": () => json({ messages: [] }) });
  const first = render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");
  await userEvent.type(screen.getByLabelText("Message the agent"), "private thought");
  window.dispatchEvent(new Event("pagehide"));
  first.unmount();

  const other = render(
    <App agents={[AGENT, SECOND]} member={{ ...MEMBER, id: "m2", email: "other@example.com" }} />,
  );
  await screen.findByText("No conversation with assistant yet.");
  expect((screen.getByLabelText("Message the agent") as HTMLInputElement).value).toBe("");
  other.unmount();

  render(<App agents={[AGENT, SECOND]} member={MEMBER} />);
  await screen.findByText("No conversation with assistant yet.");
  expect((screen.getByLabelText("Message the agent") as HTMLInputElement).value).toBe(
    "private thought",
  );
});
