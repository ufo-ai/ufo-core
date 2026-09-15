import { expect, test } from "vitest";

import { setPendingAsk, takePendingAsk, watchPendingAsk } from "@/lib/pendingAsk";

test("a pending ask reaches the composer for its own agent only", () => {
  setPendingAsk("agent-1", "Connect the calendar.", false);
  expect(takePendingAsk("agent-2", "new:agent-2")).toBe(null);
  expect(takePendingAsk("agent-1", "new:agent-1")).toEqual({
    text: "Connect the calendar.",
    send: false,
    meant: null,
    starter: null,
  });
});

test("an ask meant for one composer is not taken by another founding one", () => {
  setPendingAsk("agent-1", "deploy the site", true, "new:agent-1");
  expect(takePendingAsk("agent-1", "lane-1")).toBe(null);
  expect(takePendingAsk("agent-1", "new:agent-1")).toEqual({
    text: "deploy the site",
    send: true,
    meant: "new:agent-1",
    starter: null,
  });
});

test("an ask keyed to the agent alone is meant for the new chat screen, not a lane's own key", () => {
  setPendingAsk("agent-1", "Build it.", false);
  expect(takePendingAsk("agent-1", "history:lane-1")).toBe(null);
  expect(takePendingAsk("agent-1", "new:agent-1")).toEqual({
    text: "Build it.",
    send: false,
    meant: null,
    starter: null,
  });
});

test("a pending ask is taken once, so a later chat opens on the member's own draft", () => {
  setPendingAsk("agent-1", "set yourself up", false);
  expect(takePendingAsk("agent-1", "new:agent-1")).toEqual({
    text: "set yourself up",
    send: false,
    meant: null,
    starter: null,
  });
  expect(takePendingAsk("agent-1", "new:agent-1")).toBe(null);
});

test("an agent with no pending ask hands the composer nothing", () => {
  expect(takePendingAsk("never-asked", "new:never-asked")).toBe(null);
});

test("an ask states whether the composer sends it or stands it in the box", () => {
  setPendingAsk("agent-1", "summarise last week", true);
  expect(takePendingAsk("agent-1", "new:agent-1")).toEqual({
    text: "summarise last week",
    send: true,
    meant: null,
    starter: null,
  });
});

test("a composer already mounted is woken when an ask lands, and stops being woken once gone", () => {
  const woken: string[] = [];
  const stop = watchPendingAsk(() => woken.push("woken"));
  setPendingAsk("agent-1", "one", true);
  expect(woken).toEqual(["woken"]);

  stop();
  setPendingAsk("agent-1", "two", true);
  expect(woken).toEqual(["woken"]);
});
