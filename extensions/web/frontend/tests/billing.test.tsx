import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, PlacedWorkspace, fact, json, wire } from "./harness";

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

const LIMITED = {
  limited: true,
  balance_micro_usd: 12_340_000,
  refused_below_micro_usd: -4_000_000,
  card_on_file: true,
};

const RULE = "$100.00 when the balance falls below $25.00";

test("a set refill rule states its figures", async () => {
  wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }),
  });
  usagePage();

  expect(await screen.findByText("Billing")).toBeTruthy();
  expect(fact("Automatic refills")).toBe(RULE);
  expect(screen.getByRole("button", { name: "Stop automatic refills" })).toBeTruthy();
});

test("no refill rule reads Off", async () => {
  wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }),
  });
  usagePage();

  expect(await screen.findByText("Billing")).toBeTruthy();
  expect(fact("Automatic refills")).toBe("Off");
});

test("turning refills on posts the default rule and re-reads the billing state", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  const { calls } = wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }
          : { ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 },
      );
    },
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Automatic refills are on." });
    },
  });
  usagePage();

  await userEvent.click(await screen.findByRole("button", { name: "Refill " + RULE }));

  expect(posted).toEqual([
    { verb: "refill", kind: "billing", amount_dollars: 100, below_dollars: 25 },
  ]);
  expect(calls).toContain("/surface/web/agents/" + AGENT_ID + "/intents");
  expect(await screen.findByText("Automatic refills are on.")).toBeTruthy();
  await waitFor(() => expect(fact("Automatic refills")).toBe(RULE));
});

test("stopping refills posts both figures as null", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }
          : { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null },
      );
    },
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Automatic refills are off." });
    },
  });
  usagePage();

  await userEvent.click(await screen.findByRole("button", { name: "Stop automatic refills" }));

  expect(posted).toEqual([
    { verb: "refill", kind: "billing", amount_dollars: null, below_dollars: null },
  ]);
  await waitFor(() => expect(fact("Automatic refills")).toBe("Off"));
});

test("a workspace with no balance row has no spending limit", async () => {
  wire({
    "/workspace/usage": () => json(USAGE),
    "/ext/metronome/billing": () => json({ limited: false }),
  });
  usagePage();

  expect(await screen.findByText("This workspace has no spending limit.")).toBeTruthy();
});
