import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";

import { WorkspaceId } from "@/lib/audience";
import { MainAgentProvider } from "@/lib/mainAgent";

import {
  AGENT,
  AGENT_ID,
  PlacedWorkspace,
  type Route,
  json,
  wire,
} from "./harness";

const WORKSPACE = "7a1e1b2c-0000-4000-8000-000000000042";
const ADDRESS = "sam@acme.com";

const SET_PRODUCT_EMAIL = {
  name: "set_product_email",
  description: "Turn the speaker's product email on or off.",
  input_schema: {
    properties: { receiving: { type: "boolean", title: "Receiving" } },
    required: ["receiving"],
  },
  call: { kind: "member", action: "set_product_email", input: {} },
  label: "Set product email",
};

const LANE =
  "/surface/web/agents/" + AGENT_ID + "/actions/member/set_product_email";

function notificationsTab(routes: Record<string, Route>) {
  const wired = wire(routes);
  render(
    <WorkspaceId.Provider value={WORKSPACE}>
      <MainAgentProvider agents={[AGENT]}>
        <PlacedWorkspace view="email" />
      </MainAgentProvider>
    </WorkspaceId.Provider>,
  );
  return wired;
}

const RECEIVING = {
  address: ADDRESS,
  topics: [
    { topic: "product_news", receiving: true },
    { topic: "founder_updates", receiving: true },
  ],
  actions: [SET_PRODUCT_EMAIL],
};

test("the page states the address the setting belongs to, and that notices are not a choice", async () => {
  notificationsTab({ "/workspace/email": () => json(RECEIVING) });

  expect(await screen.findByText(ADDRESS)).toBeTruthy();
  expect(screen.getByText("Product email")).toBeTruthy();
  expect(screen.getByText("Account and billing")).toBeTruthy();
  expect(screen.getByText("Always on")).toBeTruthy();
});

test("turning product email off posts the member action and reads the answer back", async () => {
  let held = RECEIVING;
  const wired = notificationsTab({
    "/workspace/email": () => json(held),
    [LANE]: () => {
      held = {
        ...RECEIVING,
        topics: [
          { topic: "product_news", receiving: false },
          { topic: "founder_updates", receiving: true },
        ],
      };
      return json({ applied: true, message: "Product email is off." });
    },
  });

  const off = await screen.findByRole("checkbox", { name: "Product email" });
  await userEvent.click(off);

  await waitFor(() => {
    expect(wired.calls.some((url) => url.includes(LANE))).toBe(true);
  });
  const posted = wired.handler.mock.calls.find(([url]) =>
    String(url).includes(LANE),
  );
  expect(JSON.parse(String((posted?.[1] as RequestInit).body))).toEqual({
    receiving: false,
  });
  await waitFor(() => {
    expect(
      (screen.getByRole("checkbox", { name: "Product email" }) as HTMLInputElement).checked,
    ).toBe(false);
  });
});

test("the founder mailing list is absent from notification settings", async () => {
  notificationsTab({ "/workspace/email": () => json(RECEIVING) });

  expect(await screen.findByRole("checkbox", { name: "Product email" })).toBeTruthy();
  expect(screen.queryByText("Founder updates")).toBeNull();
  expect(screen.queryByText(/unsubscribe/)).toBeNull();
  expect(screen.getAllByRole("checkbox").length).toBe(1);
});


test("a deploy that sends no email offers nothing to set", async () => {
  notificationsTab({
    "/workspace/email": () =>
      json({ address: ADDRESS, topics: [], actions: [] }),
  });

  expect(
    await screen.findByText(
      "Email is not available.",
    ),
  ).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
});


test("the tab is Notifications and a failed save restores the stored checkbox", async () => {
  notificationsTab({
    "/workspace/email": () => json(RECEIVING),
    [LANE]: () => json({ applied: false, message: "Could not save." }),
  });
  const checkbox = await screen.findByRole("checkbox", { name: "Product email" }) as HTMLInputElement;
  expect(screen.getByRole("heading", { name: "Notifications" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Email" })).toBeNull();
  await userEvent.click(checkbox);
  await screen.findByText("Could not save.");
  expect(checkbox.checked).toBe(true);
});
