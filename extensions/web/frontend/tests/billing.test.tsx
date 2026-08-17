import { render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, PlacedWorkspace, fact, json, wire } from "./harness";

const USAGE = {
  window_seconds: 86400,
  total_micro_usd: 0,
  by_dimension: [],
  caps: [],
  usage: {
    selected: { tokens: 0, token_micro_usd: 0, total_micro_usd: 0 },
    all_time: { tokens: 0, token_micro_usd: 0, total_micro_usd: 0 },
    first_used_at: null,
    previous_tokens: null,
    daily: [],
    by_execution: [],
    by_model: [],
  },
  workspace: null,
};

function usagePage() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="usage" />
    </MainAgentProvider>,
  );
}

test("billing states the refusal line, the balance, and the card", async () => {
  wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () =>
      json({
        limited: true,
        balance_micro_usd: 12_340_000,
        reserve_micro_usd: 1_000_000,
        grace_micro_usd: 5_000_000,
        refused_below_micro_usd: -4_000_000,
        granted_micro_usd: 20_000_000,
        charged_micro_usd: 7_660_000,
        card_on_file: false,
      }),
  });
  usagePage();

  expect(await screen.findByText("Billing")).toBeTruthy();
  expect(fact("Turns are refused below")).toBe("-$4.00");
  expect(fact("Balance")).toBe("$12.34");
  expect(fact("Card on file")).toBe("No");
});

test("a member the billing route refuses sees no billing section", async () => {
  const { calls } = wire({
    "/workspace/usage": () => json(USAGE),
    // The route answers a refusal with a body, exactly as the handler does. An empty one would
    // let a component that ignored the status still render nothing, and prove nothing.
    "/ext/metronome/billing": () =>
      Response.json({ error: "only a workspace admin can read billing" }, { status: 403 }),
  });
  usagePage();

  expect(await screen.findByText("No spend cap is set on you.")).toBeTruthy();
  await waitFor(() => expect(calls).toContain("/ext/metronome/billing"));
  expect(screen.queryByText("Billing")).toBeNull();
  expect(screen.queryByText("Balance")).toBeNull();
});

test("a workspace with no balance row has no spending limit", async () => {
  wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () => json({ limited: false }),
  });
  usagePage();

  expect(await screen.findByText("This workspace has no spending limit.")).toBeTruthy();
});
