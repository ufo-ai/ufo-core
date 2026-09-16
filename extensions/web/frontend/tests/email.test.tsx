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

function emailTab(routes: Record<string, Route>) {
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
  emailTab({ "/workspace/email": () => json(RECEIVING) });

  expect(await screen.findByText(ADDRESS)).toBeTruthy();
  expect(screen.getByText("Product email")).toBeTruthy();
  expect(screen.getByText("Workspace notices")).toBeTruthy();
  expect(screen.getByText("Always on")).toBeTruthy();
});

test("turning product email off posts the member action and reads the answer back", async () => {
  let held = RECEIVING;
  const wired = emailTab({
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

  const off = await screen.findByRole("radio", { name: "Off" });
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
      (screen.getByRole("radio", { name: "Off" }) as HTMLElement).dataset.state,
    ).toBe("on");
  });
});

test("every topic the read returns is drawn, and the founder one says where it is changed", async () => {
  emailTab({
    "/workspace/email": () =>
      json({
        ...RECEIVING,
        topics: [
          { topic: "product_news", receiving: true },
          { topic: "founder_updates", receiving: false },
        ],
      }),
  });

  expect(await screen.findByText("Founder updates")).toBeTruthy();
  expect(
    screen.getByText(
      "Off. Use the unsubscribe link in the email to change this.",
    ),
  ).toBeTruthy();
  expect(screen.getAllByRole("radio", { name: "On" }).length).toBe(1);
});

test("a deploy that sends no email offers nothing to set", async () => {
  emailTab({
    "/workspace/email": () =>
      json({ address: ADDRESS, topics: [], actions: [] }),
  });

  expect(
    await screen.findByText(
      "This deploy sends no email, so there is nothing to set.",
    ),
  ).toBeTruthy();
  expect(screen.queryByRole("radio", { name: "Off" })).toBeNull();
});
