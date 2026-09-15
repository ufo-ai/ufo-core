import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import {
  RESTING_STATUS_MS,
  WORKING_STATUS_MS,
  resetAppStatusStore,
  useAppStatus,
  wakeAppStatus,
  type AgentStatus,
} from "@/lib/appStatusStore";

import { AGENT_ID, SECOND_ID, json, wire } from "./harness";

function status(agentId: string, held: Partial<AgentStatus>): AgentStatus {
  return {
    agent_id: agentId,
    turn: null,
    activity: null,
    last_active_at: null,
    last_failed: false,
    ...held,
  };
}

function reads(calls: string[]): number {
  return calls.filter((url) => url.includes("/api/agents/status")).length;
}

async function settle(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  resetAppStatusStore();
  vi.useRealTimers();
});

test("the read runs at the working rate while an app holds work, and at the resting rate after", async () => {
  let turn: AgentStatus["turn"] = "running";
  const { calls } = wire({
    "/api/agents/status": () => json({ statuses: [status(AGENT_ID, { turn })] }),
  });

  const view = renderHook(() => useAppStatus());
  await settle(0);
  expect(reads(calls)).toBe(1);
  expect(view.result.current.working).toBe(true);
  expect(view.result.current.statuses[AGENT_ID].turn).toBe("running");

  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(2);

  turn = null;
  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(3);
  expect(view.result.current.working).toBe(false);

  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(3);
  await settle(RESTING_STATUS_MS - WORKING_STATUS_MS);
  expect(reads(calls)).toBe(4);
});

test("a queued turn is working too", async () => {
  wire({
    "/api/agents/status": () => json({ statuses: [status(SECOND_ID, { turn: "queued" })] }),
  });

  const view = renderHook(() => useAppStatus());
  await settle(0);
  expect(view.result.current.working).toBe(true);
});

test("the last reader to leave stops the read", async () => {
  const { calls } = wire({
    "/api/agents/status": () => json({ statuses: [status(AGENT_ID, { turn: "running" })] }),
  });

  const first = renderHook(() => useAppStatus());
  const second = renderHook(() => useAppStatus());
  await settle(0);
  expect(reads(calls)).toBe(1);
  expect(second.result.current.working).toBe(true);

  second.unmount();
  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(2);

  first.unmount();
  await settle(RESTING_STATUS_MS * 3);
  expect(reads(calls)).toBe(2);
});

test("a turn started in this browser is read at once, not waited out at the resting rate", async () => {
  let turn: AgentStatus["turn"] = null;
  const { calls } = wire({
    "/api/agents/status": () => json({ statuses: [status(AGENT_ID, { turn })] }),
  });

  const view = renderHook(() => useAppStatus());
  await settle(0);
  expect(reads(calls)).toBe(1);
  expect(view.result.current.working).toBe(false);

  act(() => wakeAppStatus());
  await settle(0);
  expect(reads(calls)).toBe(2);
  expect(view.result.current.working).toBe(false);

  turn = "running";
  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(3);
  expect(view.result.current.working).toBe(true);
});

test("a stir that finds nothing working settles back to the resting rate", async () => {
  const { calls } = wire({
    "/api/agents/status": () => json({ statuses: [status(AGENT_ID, { turn: null })] }),
  });

  renderHook(() => useAppStatus());
  await settle(0);
  act(() => wakeAppStatus());
  await settle(0);
  expect(reads(calls)).toBe(2);

  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(3);
  await settle(WORKING_STATUS_MS);
  expect(reads(calls)).toBe(3);
  await settle(RESTING_STATUS_MS);
  expect(reads(calls)).toBe(4);
});

test("an answer that is not the shape it claims leaves the store asking", async () => {
  let shaped = false;
  const { calls } = wire({
    "/api/agents/status": () =>
      shaped
        ? json({ statuses: [status(AGENT_ID, { turn: "running" })] })
        : json({ ok: true }),
  });

  const view = renderHook(() => useAppStatus());
  await settle(0);
  expect(reads(calls)).toBe(1);
  expect(view.result.current.statuses).toEqual({});

  shaped = true;
  await settle(RESTING_STATUS_MS);
  expect(reads(calls)).toBe(2);
  expect(view.result.current.statuses[AGENT_ID].turn).toBe("running");
});

test("a read still in flight when the store is cleared does not land on the next one", async () => {
  let answer: (value: Response) => void = () => {};
  wire({
    "/api/agents/status": () =>
      new Promise<Response>((resolve) => {
        answer = resolve;
      }),
  });

  const view = renderHook(() => useAppStatus());
  await settle(0);
  view.unmount();
  resetAppStatusStore();

  answer(json({ statuses: [status(AGENT_ID, { turn: "running" })] }));
  await settle(0);
  expect(useAppStatusOnce()).toEqual({});
});

function useAppStatusOnce(): Readonly<Record<string, AgentStatus>> {
  const view = renderHook(() => useAppStatus());
  const held = view.result.current.statuses;
  view.unmount();
  return held;
}

test("a malformed status list does not take the credit line down with it", async () => {
  /* The guard has to be per-half: the credit line is what explains why the agents stopped, so a
     status list the route cannot render must not also hide it. */
  wire({
    "/api/agents/status": () => json({ statuses: null, out_of_credit: true }),
  });
  const { result } = renderHook(() => useAppStatus());

  await settle(1);

  expect(result.current.outOfCredit).toBe(true);
  expect(result.current.statuses).toEqual({});
});
