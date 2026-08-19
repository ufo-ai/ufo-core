import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  CHAT_ROW,
  CONVO_ID,
  MEMBER,
  StreamFake,
  TURN_ID,
  json,
  useStreamFake,
  wire,
  type Route,
} from "./harness";

beforeEach(() => {
  location.hash = "#/c/" + CONVO_ID;
  useStreamFake();
});

const CHAT_URL = "/surface/web/agents/" + AGENT.id + "/chat?conversation=" + CONVO_ID;

/** The conversation as the page reads it. `turn` is the turn the read names for the page to tail,
 *  so a payload carrying one opens the log on a live turn and the composer on a turn to stop. */
function reading(chat: Route, turn: string | null = TURN_ID) {
  return {
    "/api/chats": () => json({ chats: [CHAT_ROW] }),
    "/slots": () => json({ slots: [] }),
    "/transcript": () =>
      json({
        messages: [{ role: "user", text: "Review PR 1268." }],
        ...(turn === null ? {} : { turn }),
      }),
    "/chat": chat,
  };
}

function open() {
  render(
    <App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />,
  );
}

const stopped = () => json({ stopped: true });

function posts(handler: { mock: { calls: [string, RequestInit?][] } }): [string, RequestInit?][] {
  return handler.mock.calls.filter(([url]) => url.includes("/chat?"));
}

test("a settled conversation offers no stop", async () => {
  wire(reading(stopped, null));
  open();

  expect(await screen.findByText("Review PR 1268.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
});

test("a live turn is stopped by the header alone, and the stream's terminal ends it", async () => {
  const { handler } = wire(reading(stopped));
  open();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const stop = await screen.findByRole("button", { name: "Stop" });
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  await userEvent.click(stop);

  await waitFor(() => expect(posts(handler)).toHaveLength(1));
  const [url, init] = posts(handler)[0];
  expect(url).toBe(CHAT_URL);
  expect(init?.method).toBe("POST");
  expect(init?.headers).toEqual({ "x-ufo-stop-turn": TURN_ID });
  expect(init?.body).toBeUndefined();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();

  StreamFake.last().emit("terminal", {
    status: "cancelled",
    model: "opus",
    tokens: 3,
    cost_micro_usd: 1,
  });

  await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).toBeNull());
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
});

test("a stop that founds the next turn moves the tail onto it", async () => {
  const founded = "22222222-2222-4222-8222-222222222222";
  const { handler } = wire(reading(() => json({ stopped: true, turn_id: founded })));
  open();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  await userEvent.click(await screen.findByRole("button", { name: "Stop" }));

  await waitFor(() => expect(posts(handler)).toHaveLength(1));
  await waitFor(() => expect(StreamFake.opened.length).toBe(2));
  expect(StreamFake.last().url).toContain(founded);
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
});

test("a stop in flight swallows a second press", async () => {
  let land = () => {};
  const held = new Promise<void>((resolve) => {
    land = resolve;
  });
  const { handler } = wire(
    reading(async () => {
      await held;
      return json({ stopped: true });
    }),
  );
  open();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  const stop = await screen.findByRole("button", { name: "Stop" });
  await userEvent.click(stop);
  await waitFor(() => expect(stop.getAttribute("aria-disabled")).toBe("true"));
  await userEvent.click(stop);

  expect(posts(handler)).toHaveLength(1);
  land();
  await waitFor(() => expect(stop.getAttribute("aria-disabled")).toBeNull());
});

test("a refused stop reports as a toast and leaves the turn streaming", async () => {
  wire(reading(() => new Response("no such turn in this conversation", { status: 404 })));
  open();
  await waitFor(() => expect(StreamFake.opened.length).toBe(1));

  await userEvent.click(await screen.findByRole("button", { name: "Stop" }));

  const toast = await screen.findByRole("status");
  expect(toast.getAttribute("data-slot")).toBe("toast");
  expect(toast.textContent).toContain("The turn did not stop.");
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
});
