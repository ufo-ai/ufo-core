import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, PlacedWorkspace, WORKSPACE_ID, type Route, json, wire } from "./harness";

const CARD_PATH = "/ext/metronome/billing/card";

const CARD = { brand: "visa", last4: "4242" };

function wireBilling(routes: Record<string, Route>) {
  return wire({ [CARD_PATH]: () => json({ card: null }), ...routes });
}

function billingTab() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="billing" />
    </MainAgentProvider>,
  );
}

const WORKSPACE_ACTIONS = {
  actions: [
    {
      name: "manage_billing",
      description: "",
      input_schema: {},
      call: { kind: "workspace", name: WORKSPACE_ID, action: "manage_billing", input: {} },
      label: "Manage billing",
    },
  ],
};

const LIMITED = {
  limited: true,
  balance_micro_usd: 12_340_000,
  reserve_micro_usd: 1_000_000,
  grace_micro_usd: 5_000_000,
  refused_below_micro_usd: -4_000_000,
  granted_micro_usd: 20_000_000,
  charged_micro_usd: 7_660_000,
  purchases: [
    { at: "2026-08-20T05:26:37Z", granted_micro_usd: 5_000_000, charged_micro_usd: 5_000_000 },
    { at: "2026-08-19T22:35:48Z", granted_micro_usd: 100_000_000, charged_micro_usd: 0 },
  ],
};

const RULE = "Adding $100.00 when the balance falls below $25.00.";

test("the balance is the figure, and its refusal line shows only once it bites", async () => {
  wireBilling({ "/ext/metronome/billing": () => json(LIMITED) });
  billingTab();

  expect(await screen.findByText("$12.34")).toBeTruthy();
  expect(screen.getByText("Current balance")).toBeTruthy();
  expect(screen.queryByText(/Turns are refused until/)).toBeNull();
});

test("a stopped workspace states what has to be true before turns run again", async () => {
  wireBilling({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, balance_micro_usd: -6_430_000, refused_below_micro_usd: 0 }),
  });
  billingTab();

  expect(await screen.findByText("-$6.43")).toBeTruthy();
  expect(
    screen.getByText("Turns are refused until it is back above $0.00."),
  ).toBeTruthy();
});

test("the card on file is named, not reduced to yes", async () => {
  wireBilling({
    "/ext/metronome/billing": () => json(LIMITED),
    [CARD_PATH]: () => json({ card: CARD }),
  });
  billingTab();

  expect(await screen.findByText("visa •••• 4242")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Update" })).toBeTruthy();
});

test("the credit history states what was added and what it cost", async () => {
  wireBilling({ "/ext/metronome/billing": () => json(LIMITED) });
  billingTab();

  // The table draws a stacked variant for narrow widths, so every cell renders twice.
  expect((await screen.findAllByText("Aug 20, 2026")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("Aug 19, 2026").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$100.00").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$5.00").length).toBeGreaterThan(0);
});

test("a member the billing route refuses is told who may read it", async () => {
  wireBilling({
    "/ext/metronome/billing": () =>
      Response.json({ error: "only a workspace admin can read billing" }, { status: 403 }),
  });
  billingTab();

  expect(await screen.findByText("Only a workspace admin can read billing.")).toBeTruthy();
  expect(screen.queryByText("Current balance")).toBeNull();
});

test("a deploy carrying no billing extension says so, and claims no refusal", async () => {
  wireBilling({ "/ext/metronome/billing": () => Response.json({}, { status: 404 }) });
  billingTab();

  expect(await screen.findByText("This deploy does not carry billing.")).toBeTruthy();
  expect(screen.queryByText("Only a workspace admin can read billing.")).toBeNull();
});

test("a read that fails for any other reason says only that", async () => {
  wireBilling({ "/ext/metronome/billing": () => Response.json({}, { status: 500 }) });
  billingTab();

  expect(await screen.findByText("Billing could not be read.")).toBeTruthy();
});

test("a card the provider would not read shows as unknown, not as absent", async () => {
  wireBilling({
    "/ext/metronome/billing": () => json(LIMITED),
    [CARD_PATH]: () => json({ card: null, card_unread: true }),
  });
  billingTab();

  expect(await screen.findByText("Payment method could not be read")).toBeTruthy();
  expect(screen.getByText("$12.34")).toBeTruthy();
  expect(screen.queryByText("No payment method")).toBeNull();
  expect(
    screen.getByText("The card provider did not answer. The balance above is current."),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
});

test("no card offers the provider and holds the refill shut with its reason", async () => {
  wireBilling({
    "/ext/metronome/billing": () => json(LIMITED),
  });
  billingTab();

  expect(await screen.findByRole("button", { name: "Save a payment method" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
  expect(screen.getByText("Save a payment method to arrange refills.")).toBeTruthy();
  expect(screen.getByLabelText("Add")).toHaveProperty("disabled", true);
});

test("saving a payment method posts the card verb and states the link it answers", async () => {
  const posted: unknown[] = [];
  wireBilling({
    "/ext/metronome/billing": () => json(LIMITED),
    "/actions/workspace/": (_url, init) =>
      init?.method === "POST"
        ? (posted.push(JSON.parse(String(init?.body))),
          json({ applied: true, message: "", url: "https://billing.stripe.test/session/abc" }))
        : json(WORKSPACE_ACTIONS),
  });
  billingTab();

  await userEvent.click(await screen.findByRole("button", { name: "Save a payment method" }));

  expect(posted).toEqual([{ operation: "portal" }]);
  const link = await screen.findByRole("link", { name: "Open the billing portal" });
  expect(link.getAttribute("href")).toBe("https://billing.stripe.test/session/abc");
});

test("a card on file opens the amounts and posts the figures typed into them", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  const { calls } = wireBilling({
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }
          : { ...LIMITED, autopay_micro_usd: 250_000_000, autopay_below_micro_usd: 50_000_000 },
      );
    },
    "/actions/workspace/": (_url, init) =>
      init?.method === "POST"
        ? (posted.push(JSON.parse(String(init?.body))),
          json({ applied: true, message: "Automatic refills are on." }))
        : json(WORKSPACE_ACTIONS),
    [CARD_PATH]: () => json({ card: CARD }),
  });
  billingTab();

  const amount = await screen.findByLabelText("Add");
  await waitFor(() => expect(amount).toHaveProperty("disabled", false));
  await userEvent.clear(amount);
  await userEvent.type(amount, "250");
  const below = screen.getByLabelText("When the balance falls below");
  await userEvent.clear(below);
  await userEvent.type(below, "50");
  await userEvent.click(screen.getByRole("button", { name: "Turn on" }));

  expect(posted).toEqual([
    { operation: "autopay", autopay_dollars: 250, autopay_below_dollars: 50 },
  ]);
  expect(calls).toContain(
    "/surface/web/agents/" +
      AGENT_ID +
      "/actions/workspace/" +
      WORKSPACE_ID +
      "/manage_billing",
  );
  await waitFor(() =>
    expect(
      screen.getByText("Adding $250.00 when the balance falls below $50.00."),
    ).toBeTruthy(),
  );
});

test("an emptied amount cannot be submitted", async () => {
  wireBilling({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }),
    [CARD_PATH]: () => json({ card: CARD }),
  });
  billingTab();

  const amount = await screen.findByLabelText("Add");
  await waitFor(() => expect(amount).toHaveProperty("disabled", false));
  await userEvent.clear(amount);

  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
});

test("a set refill rule states its figures and offers only the stop", async () => {
  wireBilling({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }),
  });
  billingTab();

  expect(await screen.findByText(RULE)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(screen.queryByLabelText("Add")).toBeNull();
});

test("stopping refills posts both figures as null", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  wireBilling({
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }
          : { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null },
      );
    },
    "/actions/workspace/": (_url, init) =>
      init?.method === "POST"
        ? (posted.push(JSON.parse(String(init?.body))),
          json({ applied: true, message: "Automatic refills are off." }))
        : json(WORKSPACE_ACTIONS),
  });
  billingTab();

  await userEvent.click(await screen.findByRole("button", { name: "Stop" }));

  expect(posted).toEqual([
    { operation: "autopay", autopay_dollars: null, autopay_below_dollars: null },
  ]);
  await waitFor(() => expect(screen.getByLabelText("Add")).toBeTruthy());
});

test("the acts stay shut until the workspace projects the billing action", async () => {
  wireBilling({
    "/actions/workspace/": () => json({ actions: [] }),
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }),
    [CARD_PATH]: () => json({ card: CARD }),
  });
  billingTab();

  expect(await screen.findByText(RULE)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toHaveProperty("disabled", true);
  expect(await screen.findByRole("button", { name: "Update" })).toHaveProperty("disabled", true);
});

test("a workspace with no balance row has no spending limit", async () => {
  wireBilling({ "/ext/metronome/billing": () => json({ limited: false }) });
  billingTab();

  expect(await screen.findByText("This workspace has no spending limit.")).toBeTruthy();
});

test("the balance is drawn before the card is asked for", async () => {
  const { calls } = wireBilling({
    "/ext/metronome/billing": () => json(LIMITED),
    [CARD_PATH]: () => new Promise<Response>(() => {}),
  });
  billingTab();

  expect(await screen.findByText("$12.34")).toBeTruthy();
  expect(screen.getByText("Reading the payment method")).toBeTruthy();
  await waitFor(() => expect(calls).toContain(CARD_PATH));
  expect(calls.indexOf("/ext/metronome/billing")).toBeLessThan(calls.indexOf(CARD_PATH));
});
