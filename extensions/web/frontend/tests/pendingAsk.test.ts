import { expect, test } from "vitest";

import { setPendingAsk, takePendingAsk } from "@/lib/pendingAsk";

test("a pending ask reaches the composer for its own agent only", () => {
  setPendingAsk("agent-1", "Load the agent-setup skill and follow its instructions.");
  expect(takePendingAsk("agent-2")).toBe("");
  expect(takePendingAsk("agent-1")).toBe(
    "Load the agent-setup skill and follow its instructions.",
  );
});

test("a pending ask is taken once, so a later chat opens on the member's own draft", () => {
  setPendingAsk("agent-1", "set yourself up");
  expect(takePendingAsk("agent-1")).toBe("set yourself up");
  expect(takePendingAsk("agent-1")).toBe("");
});

test("an agent with no pending ask hands the composer nothing", () => {
  expect(takePendingAsk("never-asked")).toBe("");
});
