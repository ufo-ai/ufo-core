import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";

import { App } from "@/App";
import { MainAgentProvider } from "@/lib/mainAgent";
import { sectionHash } from "@/lib/route";
import { ConnectSurfaces, type SurfaceRow } from "@/views/Surfaces";

import { AGENT, MEMBER, json, useStreamFake, wire, type Route } from "./harness";

const INSTALL = "curl -fsSL https://ufo.example/ufo | sh";
const SMS_LINK = "sms:+14155550100&body=JOIN";
const INSTRUCTION = "Send the prefilled text to finish connecting.";

const SLACK: SurfaceRow = {
  name: "slack",
  label: "Slack",
  offered: true,
  connected: false,
  install_command: null,
};
const IMESSAGE: SurfaceRow = {
  name: "imessage",
  label: "iMessage",
  offered: true,
  connected: false,
  install_command: null,
};
const TERMINAL: SurfaceRow = {
  name: "ufo",
  label: "Terminal",
  offered: true,
  connected: true,
  install_command: INSTALL,
};

function messaging(
  surfaces: SurfaceRow[],
  posted: { url: string; body: unknown }[] = [],
  outcome: unknown = { applied: true, message: INSTRUCTION, url: SMS_LINK },
) {
  const record: Route = (url, init) => {
    posted.push({ url, body: JSON.parse(String(init?.body)) });
    return json(outcome);
  };
  return wire({
    "/workspace/surfaces$": () => json({ surfaces }),
    "/workspace/team$": () => json({ members: [MEMBER], can_add: false, actions: [] }),
    "/workspace/first-run$": () =>
      json({
        providers: [],
        mcp_servers: [],
        connectors: [{ name: "slack", label: "Slack", installed: false }],
        actions: { member: [], memory: [], enrichment_profile: [] },
        model_key_held: false,
      }),
    "/actions/surface/imessage$": () =>
      json({
        actions: [
          {
            name: "imessage_connect",
            description: "Connect a phone.",
            input_schema: { properties: { phone_number: { type: "string" } } },
            call: { kind: "surface", action: "imessage_connect", name: "imessage", input: {} },
            label: "Connect",
          },
        ],
      }),
    "/actions/surface/imessage/imessage_connect": record,
    "/transcript": () => json({ messages: [] }),
  });
}

/** A card names the channel and carries one act; the steps that finish it stand in the sheet that act
 *  opens. */
async function openChannel(label: string): Promise<HTMLElement> {
  const card = await screen.findByRole("listitem", { name: label });
  await userEvent.click(within(card).getByRole("button", { name: "Connect" }));
  return await screen.findByRole("dialog", { name: label });
}

beforeEach(() => {
  useStreamFake();
  location.hash = "";
});

test("the rail's Channels tile opens the rows in a dialog, dotted while one is unconnected", async () => {
  messaging([SLACK, IMESSAGE, TERMINAL]);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  const tile = rail.getByRole("button", { name: "Channels" });
  await waitFor(() => expect(tile.querySelector("span[aria-hidden]")).toBeTruthy());
  await userEvent.click(tile);

  const dialog = within(await screen.findByRole("dialog", { name: "Channels" }));
  expect(await dialog.findByRole("listitem", { name: "Slack" })).toBeTruthy();
  expect(dialog.getByRole("listitem", { name: "Terminal" })).toBeTruthy();
});

test("the page lists the three surfaces, each with its state or its act", async () => {
  location.hash = sectionHash("connectors");
  messaging([SLACK, IMESSAGE, TERMINAL]);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const slack = within(await screen.findByRole("listitem", { name: "Slack" }));
  const imessage = within(screen.getByRole("listitem", { name: "iMessage" }));
  const terminal = within(screen.getByRole("listitem", { name: "Terminal" }));

  expect(slack.getByRole("button", { name: "Connect" })).toBeTruthy();
  expect(slack.getByText("Mention @ufo or DM it.")).toBeTruthy();
  expect(imessage.getByText("Text ufo from your phone.")).toBeTruthy();
  expect(imessage.getByRole("button", { name: "Connect" })).toBeTruthy();
  expect(terminal.getByLabelText("Terminal connected")).toBeTruthy();
  expect(terminal.getByRole("button", { name: "Configure" })).toBeTruthy();
  expect(terminal.queryByRole("button", { name: "Connect" })).toBeNull();
  expect(terminal.getByText("Chat and run tasks from your terminal.")).toBeTruthy();

  expect(
    within(await openChannel("iMessage")).getByRole("textbox", { name: "Phone number" }),
  ).toBeTruthy();
});

test("an admin sees the Slack install act", async () => {
  location.hash = sectionHash("connectors");
  wire({
    "/workspace/surfaces$": () => json({ surfaces: [SLACK] }),
    "/workspace/team$": () =>
      json({ members: [{ ...MEMBER, admin: true }], can_add: true, actions: [] }),
    "/workspace/first-run$": () =>
      json({
        providers: [],
        mcp_servers: [],
        connectors: [],
        actions: { member: [], memory: [], enrichment_profile: [] },
        model_key_held: false,
      }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={{ ...MEMBER, admin: true }} onAgents={() => {}} />);

  expect(await screen.findByRole("button", { name: "Connect" })).toBeTruthy();
});

test("the Terminal row states the install command under it and copies it on one press", async () => {
  location.hash = sectionHash("connectors");
  messaging([{ ...TERMINAL, connected: false }]);
  const writeText = vi.fn(() => Promise.resolve());
  vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const sheet = await openChannel("Terminal");
  const terminal = within(sheet);
  await waitFor(() => expect(sheet.querySelector("code")?.textContent).toBe(INSTALL));

  await userEvent.click(terminal.getByRole("button", { name: "Copy command" }));

  await waitFor(() => expect(writeText).toHaveBeenCalledWith(INSTALL));
  expect(await terminal.findByRole("button", { name: "Copied" })).toBeTruthy();
});

test("a deploy with no public address says so on the Terminal row", async () => {
  location.hash = sectionHash("connectors");
  messaging([{ ...TERMINAL, connected: false, install_command: null }]);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const terminal = within(await openChannel("Terminal"));
  expect(terminal.getByText("This deploy has no public address.")).toBeTruthy();
});

test("the iMessage act takes a phone number, posts it, and shows the Messages link", async () => {
  location.hash = sectionHash("connectors");
  const posted: { url: string; body: unknown }[] = [];
  messaging([IMESSAGE], posted);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const imessage = within(await openChannel("iMessage"));
  await userEvent.type(imessage.getByRole("textbox", { name: "Phone number" }), "+1 415 555 0100");
  await userEvent.click(imessage.getByRole("button", { name: "Send code" }));

  expect(await imessage.findByText(INSTRUCTION)).toBeTruthy();
  expect(imessage.queryByRole("link")).toBeNull();
  expect(imessage.queryByRole("img")).toBeNull();
  expect(posted).toEqual([
    {
      url: "/surface/web/agents/" + AGENT.id + "/actions/surface/imessage/imessage_connect",
      body: { phone_number: "+1 415 555 0100" },
    },
  ]);
});

test("a refused code is said in the toast", async () => {
  location.hash = sectionHash("connectors");
  messaging([IMESSAGE], [], { applied: false, message: "That number cannot receive texts." });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const imessage = within(await openChannel("iMessage"));
  await userEvent.type(imessage.getByRole("textbox", { name: "Phone number" }), "+1 415 555 0100");
  await userEvent.click(imessage.getByRole("button", { name: "Send code" }));

  expect(await screen.findByText("That number cannot receive texts.")).toBeTruthy();
  expect(imessage.queryByText(INSTRUCTION)).toBeNull();
});

test("a deploy with no iMessage provider says so instead of asking for a number", async () => {
  location.hash = sectionHash("connectors");
  messaging([{ ...IMESSAGE, offered: false }]);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const imessage = within(await screen.findByRole("listitem", { name: "iMessage" }));
  expect(imessage.getByText("This deploy has no iMessage provider.")).toBeTruthy();
  expect(imessage.queryByRole("button", { name: "Connect" })).toBeNull();
});

test("a hidden row is not drawn", async () => {
  messaging([SLACK, IMESSAGE, TERMINAL]);
  render(
    <MainAgentProvider agents={[AGENT]}>
      <ConnectSurfaces agent={AGENT} member={MEMBER} hidden={["slack"]} onRefused={() => {}} />
    </MainAgentProvider>,
  );

  expect(await screen.findByRole("listitem", { name: "iMessage" })).toBeTruthy();
  expect(screen.getByRole("listitem", { name: "Terminal" })).toBeTruthy();
  expect(screen.queryByRole("listitem", { name: "Slack" })).toBeNull();
});

test("a surface this deploy does not offer states so instead of a Connect", async () => {
  location.hash = sectionHash("connectors");
  messaging([{ ...SLACK, offered: false }, IMESSAGE, TERMINAL]);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const slack = within(await screen.findByRole("listitem", { name: "Slack" }));
  expect(slack.getByText("This deploy has no Slack surface.")).toBeTruthy();
  expect(slack.queryByRole("button", { name: "Connect" })).toBeNull();
});

test("the rail's Channels tile leaves the dot off an unofferable row and holds one read", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const { calls } = messaging([
    { ...SLACK, offered: false },
    { ...IMESSAGE, offered: false },
    TERMINAL,
  ]);
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  const tile = rail.getByRole("button", { name: "Channels" });
  const reads = () => calls.filter((url) => url.includes("/workspace/surfaces")).length;
  await waitFor(() => expect(reads()).toBe(1));
  expect(tile.querySelector("span[aria-hidden]")).toBeNull();

  await act(async () => {
    vi.advanceTimersByTime(10_000);
  });
  expect(reads()).toBe(1);
  expect(tile.querySelector("span[aria-hidden]")).toBeNull();
  vi.useRealTimers();
});

test("a refused surfaces read states the refusal once and settles", async () => {
  wire({
    "/workspace/surfaces$": () => new Response("nope", { status: 503 }),
    "/workspace/team$": () => json({ members: [MEMBER], can_add: false, actions: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  const rail = within(await screen.findByRole("navigation", { name: "Tabs" }));
  await userEvent.click(rail.getByRole("button", { name: "Channels" }));

  expect(await screen.findByText("Error 503 — reload to retry.")).toBeTruthy();
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getAllByText("Error 503 — reload to retry.")).toHaveLength(1);
});
