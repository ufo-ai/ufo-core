import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, PlacedWorkspace, json, wire } from "./harness";

function billingTab() {
  render(
    <MainAgentProvider agents={[AGENT]}>
      <PlacedWorkspace view="billing" />
    </MainAgentProvider>,
  );
}

const LIMITED = {
  limited: true,
  balance_micro_usd: 12_340_000,
  reserve_micro_usd: 1_000_000,
  grace_micro_usd: 5_000_000,
  refused_below_micro_usd: -4_000_000,
  granted_micro_usd: 20_000_000,
  charged_micro_usd: 7_660_000,
  card: { brand: "visa", last4: "4242" },
  purchases: [
    { at: "2026-08-20T05:26:37Z", granted_micro_usd: 5_000_000, charged_micro_usd: 5_000_000 },
    { at: "2026-08-19T22:35:48Z", granted_micro_usd: 100_000_000, charged_micro_usd: 0 },
  ],
};

const RULE = "Adding $100.00 when the balance falls below $25.00.";

test("the balance is the figure, and its refusal line shows only once it bites", async () => {
  wire({ "/ext/metronome/billing": () => json({ ...LIMITED, card: null }) });
  billingTab();

  expect(await screen.findByText("$12.34")).toBeTruthy();
  expect(screen.getByText("Current balance")).toBeTruthy();
  expect(screen.queryByText(/Turns are refused until/)).toBeNull();
});

test("a stopped workspace states what has to be true before turns run again", async () => {
  wire({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, balance_micro_usd: -6_430_000, refused_below_micro_usd: 0, card: null }),
  });
  billingTab();

  expect(await screen.findByText("-$6.43")).toBeTruthy();
  expect(
    screen.getByText("Turns are refused until it is back above $0.00."),
  ).toBeTruthy();
});

test("the card on file is named, not reduced to yes", async () => {
  wire({ "/ext/metronome/billing": () => json(LIMITED) });
  billingTab();

  expect(await screen.findByText("visa •••• 4242")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Update" })).toBeTruthy();
});

test("the credit history states what was added and what it cost", async () => {
  wire({ "/ext/metronome/billing": () => json(LIMITED) });
  billingTab();

  // The table draws a stacked variant for narrow widths, so every cell renders twice.
  expect((await screen.findAllByText("Aug 20, 2026")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("Aug 19, 2026").length).toBeGreaterThan(0);
  // The $100 grant charged nothing, so a grant reads apart from a purchase.
  expect(screen.getAllByText("$100.00").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$5.00").length).toBeGreaterThan(0);
});

test("a member the billing route refuses is told who may read it", async () => {
  // The route answers a refusal with a body, exactly as the handler does. Claiming the workspace
  // has no limit would be a guess: a refused read knows nothing about the balance.
  wire({
    "/ext/metronome/billing": () =>
      Response.json({ error: "only a workspace admin can read billing" }, { status: 403 }),
  });
  billingTab();

  expect(await screen.findByText("Only a workspace admin can read billing.")).toBeTruthy();
  expect(screen.queryByText("Current balance")).toBeNull();
});

test("a deploy carrying no billing extension says so, and claims no refusal", async () => {
  // The tab is drawn on every deploy, but the route is mounted only for a loaded manifest, so a
  // pack without the billing extension answers 404. Calling that an authorization refusal tells an
  // admin they lack a permission on a deploy that has nothing to permit.
  wire({ "/ext/metronome/billing": () => Response.json({}, { status: 404 }) });
  billingTab();

  expect(await screen.findByText("This deploy does not carry billing.")).toBeTruthy();
  expect(screen.queryByText("Only a workspace admin can read billing.")).toBeNull();
});

test("a read that fails for any other reason says only that", async () => {
  wire({ "/ext/metronome/billing": () => Response.json({}, { status: 500 }) });
  billingTab();

  expect(await screen.findByText("Billing could not be read.")).toBeTruthy();
});

test("a card the provider would not read shows as unknown, not as absent", async () => {
  // "No payment method" would invite saving one on a guess about the provider's own state, and the
  // balance beside it is core's — so it still reads.
  wire({
    "/ext/metronome/billing": () => json({ ...LIMITED, card: null, card_unread: true }),
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
  wire({
    "/ext/metronome/billing": () => json({ ...LIMITED, card: null }),
  });
  billingTab();

  expect(await screen.findByRole("button", { name: "Save a payment method" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
  expect(screen.getByText("Save a payment method to arrange refills.")).toBeTruthy();
  expect(screen.getByLabelText("Add")).toHaveProperty("disabled", true);
});

test("saving a payment method posts the card verb and states the link it answers", async () => {
  const posted: unknown[] = [];
  wire({
    "/ext/metronome/billing": () => json({ ...LIMITED, card: null }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "", url: "https://billing.stripe.test/session/abc" });
    },
  });
  billingTab();

  await userEvent.click(await screen.findByRole("button", { name: "Save a payment method" }));

  expect(posted).toEqual([{ verb: "save_card", kind: "billing" }]);
  const link = await screen.findByRole("link", { name: "Open the billing portal" });
  expect(link.getAttribute("href")).toBe("https://billing.stripe.test/session/abc");
});

test("a card on file opens the amounts and posts the figures typed into them", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  const { calls } = wire({
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }
          : { ...LIMITED, autopay_micro_usd: 250_000_000, autopay_below_micro_usd: 50_000_000 },
      );
    },
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Automatic refills are on." });
    },
  });
  billingTab();

  const amount = await screen.findByLabelText("Add");
  await userEvent.clear(amount);
  await userEvent.type(amount, "250");
  const below = screen.getByLabelText("When the balance falls below");
  await userEvent.clear(below);
  await userEvent.type(below, "50");
  await userEvent.click(screen.getByRole("button", { name: "Turn on" }));

  expect(posted).toEqual([
    { verb: "refill", kind: "billing", amount_dollars: 250, below_dollars: 50 },
  ]);
  expect(calls).toContain("/surface/web/agents/" + AGENT_ID + "/intents");
  await waitFor(() =>
    expect(
      screen.getByText("Adding $250.00 when the balance falls below $50.00."),
    ).toBeTruthy(),
  );
});

test("an emptied amount cannot be submitted", async () => {
  wire({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }),
  });
  billingTab();

  await userEvent.clear(await screen.findByLabelText("Add"));

  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
});

test("a set refill rule states its figures and offers only the stop", async () => {
  wire({
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
  wire({
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
  billingTab();

  await userEvent.click(await screen.findByRole("button", { name: "Stop" }));

  expect(posted).toEqual([
    { verb: "refill", kind: "billing", amount_dollars: null, below_dollars: null },
  ]);
  await waitFor(() => expect(screen.getByLabelText("Add")).toBeTruthy());
});

test("a workspace with no balance row has no spending limit", async () => {
  wire({ "/ext/metronome/billing": () => json({ limited: false }) });
  billingTab();

  expect(await screen.findByText("This workspace has no spending limit.")).toBeTruthy();
});
