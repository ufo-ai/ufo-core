/** The coding-account acts, tested where they live: one component the first run and the settings
 *  panel both mount, so the flow is pinned once rather than once per screen. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { ConnectAccount } from "@/views/ConnectAccount";
import { json, wire, type Route } from "./harness";

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
  render(<ConnectAccount onConnected={onConnected} />);
  return wired;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

test("both accounts are offered, whatever the member already holds", async () => {
  open({ "/workspace/accounts": () => accounts(true, false) });

  expect(await screen.findByText("ChatGPT")).toBeTruthy();
  expect(screen.getByText("Claude")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Replace" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeTruthy();
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

test("an account the grant is refused for says why and cannot be confirmed", async () => {
  open({ "/openai/device$": () => json({ error: REFUSED_LINE }) });

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);

  expect(await screen.findByText(REFUSED_LINE)).toBeTruthy();
  expect(screen.getByRole("button", { name: "OK" }).getAttribute("disabled")).not.toBeNull();
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

  expect(screen.getByRole("button", { name: "OK" }).getAttribute("disabled")).not.toBeNull();

  claim = { status: "connected" };
  held = true;

  await screen.findByText("ChatGPT connected.", undefined, { timeout: 4000 });
  const ok = screen.getByRole("button", { name: "OK" });
  expect(ok.getAttribute("disabled")).toBeNull();
  expect(screen.getByRole("dialog")).toBeTruthy();

  await userEvent.click(ok);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByRole("img", { name: "ChatGPT connected" })).toBeTruthy();
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
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));

  await screen.findByText("Claude connected.");
  await userEvent.click(screen.getByRole("button", { name: "OK" }));
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
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));

  expect(await screen.findByText(REFUSED_LINE)).toBeTruthy();
  expect(screen.getByRole("dialog")).toBeTruthy();
});

/** The reason this is a component rather than a step: a member who rotated or revoked a key comes
 *  back through settings to replace it, and dropping the old one is the same act either way. */
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

/** The first run draws the same accounts as the acts of that flow: one full-width act per provider,
 *  and a connected account standing as the completed signal rather than as something to manage —
 *  a member is connecting there, not maintaining, so nothing offers to drop what they just gave. */
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
  render(<ConnectAccount stacked />);

  expect(await screen.findByText("ChatGPT connected")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Connect Claude" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Replace" })).toBeNull();
  expect(wired).toBeTruthy();
});

/** Cancel is the way out of an asking that did not land, and it must not report one: a member who
 *  opened the dialog and thought better of it holds exactly what they held before. */
test("leaving an asking that did not land reports nothing", async () => {
  const settled = vi.fn();
  open({ "/openai/device$": () => json(DEVICE), "/openai/device/poll": () => json({ status: "pending" }) }, settled);

  await userEvent.click((await screen.findAllByRole("button", { name: "Connect" }))[0]);
  await screen.findByText(DEVICE.user_code);
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(settled).not.toHaveBeenCalled();
});

/** An account that landed is held however the member leaves the dialog — the connection happened at
 *  the provider, and Cancel is not an undo for it. */
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
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(await screen.findByRole("img", { name: "ChatGPT connected" })).toBeTruthy();
  expect(settled).toHaveBeenCalled();
}, 10_000);

/** A 200 carrying something that is not JSON — a proxy's error page, a sign-in page served where a
 *  payload belongs — is read as a workspace that could not be reached. Left unguarded the parse
 *  rejects into nothing that can handle it, which fails the run without failing a test. */
test("a body that will not parse is refused rather than thrown", async () => {
  open({ "/workspace/accounts": () => new Response("file body", { status: 200 }) });

  expect(await screen.findByText("Could not reach the workspace. Try again.")).toBeTruthy();
});
