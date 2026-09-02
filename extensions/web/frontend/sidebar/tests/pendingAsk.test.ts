import { expect, test } from "vitest";

import { setPendingAsk, takePendingAsk, watchPendingAsk } from "@/lib/pendingAsk";

test("a pending ask reaches the composer for its own agent only", () => {
  setPendingAsk("agent-1", "Connect the calendar.", false);
  expect(takePendingAsk("agent-2")).toBe(null);
  expect(takePendingAsk("agent-1")).toEqual({
    text: "Connect the calendar.",
    send: false,
  });
});

test("a pending ask is taken once, so a later chat opens on the member's own draft", () => {
  setPendingAsk("agent-1", "set yourself up", false);
  expect(takePendingAsk("agent-1")).toEqual({ text: "set yourself up", send: false });
  expect(takePendingAsk("agent-1")).toBe(null);
});

test("an agent with no pending ask hands the composer nothing", () => {
  expect(takePendingAsk("never-asked")).toBe(null);
});

/** Words the member committed themselves carry the send, so the composer that takes them knows to
 *  send rather than to stand them in the box. */
test("an ask states whether the composer sends it or stands it in the box", () => {
  setPendingAsk("agent-1", "summarise last week", true);
  expect(takePendingAsk("agent-1")).toEqual({ text: "summarise last week", send: true });
});

/** A composer already on the screen when the ask lands never mounts again to read it. */
test("a composer already mounted is woken when an ask lands, and stops being woken once gone", () => {
  const woken: string[] = [];
  const stop = watchPendingAsk(() => woken.push("woken"));
  setPendingAsk("agent-1", "one", true);
  expect(woken).toEqual(["woken"]);

  stop();
  setPendingAsk("agent-1", "two", true);
  expect(woken).toEqual(["woken"]);
});
