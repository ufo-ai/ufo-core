import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { SheetHost } from "@/components/ui/sheet";
import { ConnectAccount } from "@/views/ConnectAccount";
import { json, wire, type Route } from "./harness";

/** The sheet stands beside the rows rather than over them, so both its Connect and the row's are in
 *  the tree and a press has to name which. */
function asking(): HTMLElement {
  return screen.getByRole("dialog");
}

const DEVICE = { user_code: "HY0H-0FOKK", verification_uri: "https://auth.test/device", interval: 1 };
const REFUSED_LINE = "ChatGPT did not accept that.";

function accounts(openai = false, anthropic = false) {
  return json({
    accounts: [
      { provider: "openai", label: "ChatGPT", connected: openai },
      { provider: "anthropic", label: "Claude", connected: anthropic },
    ],
  });
}

function open(routes: Record<string, Route> = {}, onConnected = () => {}) {
  const wired = wire({ "/workspace/accounts": () => accounts(), ...routes });
  render(
    <SheetHost>
      <ConnectAccount onConnected={onConnected} />
    </SheetHost>,
  );
  return wired;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

test("both accounts are offered, whatever the member already holds", async () => {
  open({ "/workspace/accounts": () => accounts(true, false) });

  expect(await screen.findByText("ChatGPT")).toBeTruthy();
  expect(screen.getByText("Claude")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Configure" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
});

test("every coding provider states the coding subagent it is available in", async () => {
  open({ "/workspace/accounts": () => accounts(true, false) });

  expect(await screen.findByText("ChatGPT")).toBeTruthy();
  expect(screen.getAllByText("Available in the coding subagent.")).toHaveLength(2);
});

test("the first run's stacked shape draws no availability chip", async () => {
  wire({ "/workspace/accounts": () => accounts() });
  render(
    <SheetHost>
      <ConnectAccount stacked />
    </SheetHost>,
  );

  expect(await screen.findByRole("button", { name: "Connect ChatGPT" })).toBeTruthy();
  expect(screen.queryByText("Available in the coding subagent.")).toBeNull();
});

test("the ChatGPT asking draws the code and the setting the grant needs", async () => {
  open({ "/openai/device$": () => json(DEVICE), "/openai/device/poll": () => json({ status: "pending" }) });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);

  expect(await screen.findByText(DEVICE.user_code)).toBeTruthy();
  expect(screen.getByText("Turn on device code authorization for your account.")).toBeTruthy();
  const settings = screen.getByRole("link", { name: /ChatGPT settings/ });
  expect(settings.getAttribute("href")).toBe("https://chatgpt.com/#settings/Security");
  expect(screen.getByRole("link", { name: /Open ChatGPT/ }).getAttribute("href")).toBe(
    DEVICE.verification_uri,
  );
});

test("an account the grant is refused for says why and the asking stands", async () => {
  open({ "/openai/device$": () => json({ error: REFUSED_LINE }) });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);

  expect(await screen.findByText(REFUSED_LINE)).toBeTruthy();
  expect(within(asking()).queryByText(/connected\./)).toBeNull();
});

test("an approved grant closes the asking and states the account", async () => {
  let claim: unknown = { status: "pending" };
  let held = false;
  open({
    "/workspace/accounts": () => accounts(held, false),
    "/openai/device$": () => json(DEVICE),
    "/openai/device/poll": () => json(claim),
  });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);
  await screen.findByText(DEVICE.user_code);

  expect(within(asking()).queryByText(/connected\./)).toBeNull();

  claim = { status: "connected" };
  held = true;

  await screen.findByText("ChatGPT connected.", undefined, { timeout: 4000 });
  expect(screen.getByRole("dialog")).toBeTruthy();

  await userEvent.click(within(asking()).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByLabelText("ChatGPT connected")).toBeTruthy();
}, 10_000);

test("a session that ended says so rather than blaming the network", async () => {
  open({ "/openai/device$": () => new Response("", { status: 401 }) });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);

  expect(
    await screen.findByText("Your session ended. Sign in again to connect an account."),
  ).toBeTruthy();
});

test("the Claude asking takes the code the member pastes back", async () => {
  const bodies: string[] = [];
  let held = false;
  open({
    "/workspace/accounts": () => accounts(false, held),
    "/anthropic/authorize": () => json({ url: "https://claude.test/authorize" }),
    "/anthropic/code": (_url, init) => {
      bodies.push(String(init?.body));
      held = true;
      return json({ status: "connected" });
    },
  });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[1]);
  expect((await screen.findByRole("link", { name: /Open Claude/ })).getAttribute("href")).toBe(
    "https://claude.test/authorize",
  );
  await userEvent.type(screen.getByLabelText("Authorization code"), "granted#sealed");
  await userEvent.click(within(asking()).getByRole("button", { name: "Connect" }));

  await screen.findByText("Claude connected.");
  await userEvent.click(within(asking()).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(bodies).toEqual(["code=granted%23sealed"]);
});

test("a refused paste states its line and holds the asking open", async () => {
  open({
    "/anthropic/authorize": () => json({ url: "https://claude.test/authorize" }),
    "/anthropic/code": () => json({ status: "refused", message: REFUSED_LINE }),
  });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[1]);
  await screen.findByRole("link", { name: /Open Claude/ });
  await userEvent.type(screen.getByLabelText("Authorization code"), "granted#sealed");
  await userEvent.click(within(asking()).getByRole("button", { name: "Connect" }));

  expect(await screen.findByText(REFUSED_LINE)).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
});

test("disconnecting drops the account and offers to connect again", async () => {
  const dropped: string[] = [];
  let held = true;
  open({
    "/workspace/accounts": () => accounts(held, false),
    "/accounts/openai/disconnect": (url) => {
      dropped.push(url);
      held = false;
      return json({ status: "disconnected" });
    },
  });

  await userEvent.click(await screen.findByRole("button", { name: "Disconnect" }));

  await waitFor(() => expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull());
  expect(dropped).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: "Connect" })).toHaveLength(2);
});

test("the polling stops when the asking is closed", async () => {
  const polls: string[] = [];
  open({
    "/openai/device$": () => json(DEVICE),
    "/openai/device/poll": (url) => {
      polls.push(url);
      return json({ status: "pending" });
    },
  });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);
  await screen.findByText(DEVICE.user_code);
  await waitFor(() => expect(polls.length).toBeGreaterThan(0), { timeout: 4000 });

  fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const asked = polls.length;

  await new Promise((settle) => setTimeout(settle, DEVICE.interval * 2500));
  expect(polls).toHaveLength(asked);
}, 10_000);

test("the first run's shape names each provider on its own act and signals what landed", async () => {
  const wired = wire({
    "/workspace/accounts": () =>
      json({
        accounts: [
          { provider: "openai", label: "ChatGPT", connected: true },
          { provider: "anthropic", label: "Claude", connected: false },
        ],
      }),
  });
  render(
    <SheetHost>
      <ConnectAccount stacked />
    </SheetHost>,
  );

  expect(await screen.findByText("ChatGPT connected")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Connect Claude" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Configure" })).toBeNull();
  expect(wired).toBeTruthy();
});

test("leaving an asking that did not land reports nothing", async () => {
  const settled = vi.fn();
  open({ "/openai/device$": () => json(DEVICE), "/openai/device/poll": () => json({ status: "pending" }) }, settled);

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);
  await screen.findByText(DEVICE.user_code);
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(settled).not.toHaveBeenCalled();
});

test("an account that landed is kept even when the member leaves by Cancel", async () => {
  const settled = vi.fn();
  let held = false;
  open(
    {
      "/workspace/accounts": () => accounts(held, false),
      "/openai/device$": () => json(DEVICE),
      "/openai/device/poll": () => {
        held = true;
        return json({ status: "connected" });
      },
    },
    settled,
  );

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);
  await screen.findByText("ChatGPT connected.", undefined, { timeout: 4000 });
  await userEvent.click(screen.getByRole("button", { name: "Close" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByLabelText("ChatGPT connected")).toBeTruthy();
  expect(settled).toHaveBeenCalled();
}, 10_000);

test("a body that will not parse is refused rather than thrown", async () => {
  open({ "/workspace/accounts": () => new Response("file body", { status: 200 }) });

  expect(await screen.findByText("Could not reach the workspace. Try again.")).toBeTruthy();
});
