import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { WorkspaceId } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";

import { AGENT, AGENT_ID, PlacedWorkspace, type Route, json, wire } from "./harness";

const WORKSPACE = "7a1e1b2c-0000-4000-8000-000000000042";

const MANAGE_BILLING = {
  name: "manage_billing",
  description: "Read or change how this workspace pays.",
  input_schema: {
    properties: {
      operation: { type: "string", title: "Operation", enum: ["status", "portal", "autopay"] },
      autopay_dollars: { anyOf: [{ type: "integer" }, { type: "null" }], default: null },
      autopay_below_dollars: { anyOf: [{ type: "integer" }, { type: "null" }], default: null },
    },
    required: ["operation"],
  },
  call: { kind: "workspace", action: "manage_billing", name: WORKSPACE, input: {} },
  label: "Manage billing",
};

const WORKSPACE_READ = "/actions/workspace/" + WORKSPACE + "$";

const WORKSPACE_ACTIONS: Record<string, Route> = {
  [WORKSPACE_READ]: () => json({ actions: [MANAGE_BILLING] }),
};

const BILLING_LANE =
  "/surface/web/agents/" + AGENT_ID + "/actions/workspace/" + WORKSPACE + "/manage_billing";

function billingTab(routes: Record<string, Route>) {
  const wired = wire({ ...WORKSPACE_ACTIONS, ...routes });
  render(
    <WorkspaceId.Provider value={WORKSPACE}>
      <MainAgentProvider agents={[AGENT]}>
        <PlacedWorkspace view="billing" />
      </MainAgentProvider>
    </WorkspaceId.Provider>,
  );
  return wired;
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
  billingTab({ "/ext/metronome/billing": () => json({ ...LIMITED, card: null }) });

  expect(await screen.findByText("$12.34")).toBeTruthy();
  expect(screen.getByText("Current balance")).toBeTruthy();
  expect(screen.queryByText(/Turns are refused until/)).toBeNull();
});

test("a stopped workspace states what has to be true before turns run again", async () => {
  billingTab({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, balance_micro_usd: -6_430_000, refused_below_micro_usd: 0, card: null }),
  });

  expect(await screen.findByText("-$6.43")).toBeTruthy();
  expect(
    screen.getByText("Turns are refused until it is back above $0.00."),
  ).toBeTruthy();
});

test("the card on file is named, not reduced to yes", async () => {
  billingTab({ "/ext/metronome/billing": () => json(LIMITED) });

  expect(await screen.findByText("visa •••• 4242")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Update" })).toBeTruthy();
});

test("the credit history states what was added and what it cost", async () => {
  billingTab({ "/ext/metronome/billing": () => json(LIMITED) });

  // The table draws a stacked variant for narrow widths, so every cell renders twice.
  expect((await screen.findAllByText("Aug 20, 2026")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("Aug 19, 2026").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$100.00").length).toBeGreaterThan(0);
  expect(screen.getAllByText("$5.00").length).toBeGreaterThan(0);
});

test("a member the billing route refuses is told who may read it", async () => {
  billingTab({
    "/ext/metronome/billing": () =>
      Response.json({ error: "only a workspace admin can read billing" }, { status: 403 }),
  });

  expect(await screen.findByText("Only a workspace admin can read billing.")).toBeTruthy();
  expect(screen.queryByText("Current balance")).toBeNull();
});

test("a deploy carrying no billing extension says so, and claims no refusal", async () => {
  billingTab({ "/ext/metronome/billing": () => Response.json({}, { status: 404 }) });

  expect(await screen.findByText("This deploy does not carry billing.")).toBeTruthy();
  expect(screen.queryByText("Only a workspace admin can read billing.")).toBeNull();
});

test("a read that fails for any other reason says only that", async () => {
  billingTab({ "/ext/metronome/billing": () => Response.json({}, { status: 500 }) });

  expect(await screen.findByText("Billing could not be read.")).toBeTruthy();
});

test("a card the provider would not read shows as unknown, not as absent", async () => {
  billingTab({
    "/ext/metronome/billing": () => json({ ...LIMITED, card: null, card_unread: true }),
  });

  expect(await screen.findByText("Payment method could not be read")).toBeTruthy();
  expect(screen.getByText("$12.34")).toBeTruthy();
  expect(screen.queryByText("No payment method")).toBeNull();
  expect(
    screen.getByText("The card provider did not answer. The balance above is current."),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
});

test("no card offers the provider and holds the refill shut with its reason", async () => {
  billingTab({
    "/ext/metronome/billing": () => json({ ...LIMITED, card: null }),
  });

  expect(await screen.findByRole("button", { name: "Save a payment method" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
  expect(screen.getByText("Save a payment method to arrange refills.")).toBeTruthy();
  expect(screen.getByLabelText("Add")).toHaveProperty("disabled", true);
});

test("saving a payment method posts the portal operation and states the link it answers", async () => {
  const posted: unknown[] = [];
  billingTab({
    "/ext/metronome/billing": () => json({ ...LIMITED, card: null }),
    "/manage_billing": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "", url: "https://billing.stripe.test/session/abc" });
    },
  });

  const save = await screen.findByRole("button", { name: "Save a payment method" });
  await waitFor(() => expect(save).toHaveProperty("disabled", false));
  await userEvent.click(save);

  expect(posted).toEqual([{ operation: "portal" }]);
  const link = await screen.findByRole("link", { name: "Open the billing portal" });
  expect(link.getAttribute("href")).toBe("https://billing.stripe.test/session/abc");
});

test("a card on file opens the amounts and posts the figures typed into them", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  const { calls } = billingTab({
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }
          : { ...LIMITED, autopay_micro_usd: 250_000_000, autopay_below_micro_usd: 50_000_000 },
      );
    },
    "/manage_billing": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Automatic refills are on." });
    },
  });

  const amount = await screen.findByLabelText("Add");
  await userEvent.clear(amount);
  await userEvent.type(amount, "250");
  const below = screen.getByLabelText("When the balance falls below");
  await userEvent.clear(below);
  await userEvent.type(below, "50");
  const turnOn = screen.getByRole("button", { name: "Turn on" });
  await waitFor(() => expect(turnOn).toHaveProperty("disabled", false));
  await userEvent.click(turnOn);

  expect(posted).toEqual([
    { operation: "autopay", autopay_dollars: 250, autopay_below_dollars: 50 },
  ]);
  expect(calls).toContain(BILLING_LANE);
  await waitFor(() =>
    expect(
      screen.getByText("Adding $250.00 when the balance falls below $50.00."),
    ).toBeTruthy(),
  );
});

test("an emptied amount cannot be submitted", async () => {
  billingTab({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null }),
  });

  await userEvent.clear(await screen.findByLabelText("Add"));

  expect(screen.getByRole("button", { name: "Turn on" })).toHaveProperty("disabled", true);
});

test("a set refill rule states its figures and offers only the stop", async () => {
  billingTab({
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }),
  });

  expect(await screen.findByText(RULE)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(screen.queryByLabelText("Add")).toBeNull();
});

test("stopping refills posts both figures as null", async () => {
  const posted: unknown[] = [];
  let reads = 0;
  billingTab({
    "/ext/metronome/billing": () => {
      reads += 1;
      return json(
        reads === 1
          ? { ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }
          : { ...LIMITED, autopay_micro_usd: null, autopay_below_micro_usd: null },
      );
    },
    "/manage_billing": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Automatic refills are off." });
    },
  });

  const stop = await screen.findByRole("button", { name: "Stop" });
  await waitFor(() => expect(stop).toHaveProperty("disabled", false));
  await userEvent.click(stop);

  expect(posted).toEqual([
    { operation: "autopay", autopay_dollars: null, autopay_below_dollars: null },
  ]);
  await waitFor(() => expect(screen.getByLabelText("Add")).toBeTruthy());
});

test("the acts stay shut until the workspace projects the billing action", async () => {
  billingTab({
    [WORKSPACE_READ]: () => json({ actions: [] }),
    "/ext/metronome/billing": () =>
      json({ ...LIMITED, autopay_micro_usd: 100_000_000, autopay_below_micro_usd: 25_000_000 }),
  });

  expect(await screen.findByText(RULE)).toBeTruthy();
  expect(screen.getByRole("button", { name: "Stop" })).toHaveProperty("disabled", true);
  expect(screen.getByRole("button", { name: "Update" })).toHaveProperty("disabled", true);
});

test("a workspace with no balance row has no spending limit", async () => {
  billingTab({ "/ext/metronome/billing": () => json({ limited: false }) });

  expect(await screen.findByText("This workspace has no spending limit.")).toBeTruthy();
});
