import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, json, MEMBER, useStreamFake, wire } from "./harness";

beforeEach(() => {
  useStreamFake();
});

const ADMIN = { ...MEMBER, admin: true };

/** The add as the member collection projects it: the press echoes this call template rather than
 *  authoring one of its own. */
const MEMBER_ACTIONS = {
  actions: [
    {
      name: "add_member",
      description: "",
      input_schema: {},
      call: { kind: "member", action: "add_member", input: {} },
      label: "Add member",
    },
  ],
};

async function openInvite() {
  await userEvent.click(await screen.findByRole("button", { name: "Invite people" }));
  const dialog = await screen.findByRole("dialog", { name: "Invite people" });
  return within(dialog).getByRole("button", { name: "Send invite" });
}

test("an admin invites a colleague from the sidebar, on the main agent's own lane", async () => {
  const bodies: string[] = [];
  const urls: string[] = [];
  wire({
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": (url, init) => {
      urls.push(url);
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Added new@example.com." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const send = await openInvite();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "new@example.com");
  await userEvent.click(screen.getByRole("checkbox", { name: "Admin" }));
  await userEvent.click(send);

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(urls[0]).toContain("/agents/" + AGENT.id + "/actions/member/add_member");
  expect(JSON.parse(bodies[0])).toEqual({ email: "new@example.com", admin: true });
  expect(await screen.findByText("Added new@example.com.")).toBeTruthy();
});

test("the invite holds open once an address lands, and the field is empty for the next one", async () => {
  wire({
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": () => json({ applied: true, message: "Added one@example.com." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const send = await openInvite();
  const field = screen.getByPlaceholderText("email@work.com");
  await userEvent.type(field, "one@example.com");
  await userEvent.click(send);

  expect(await screen.findByText("Added one@example.com.")).toBeTruthy();
  expect((field as HTMLInputElement).value).toBe("");
});

test("the act stays shut until an address is typed", async () => {
  wire({ "/actions/member$": () => json(MEMBER_ACTIONS) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const send = await openInvite();
  expect(send.hasAttribute("disabled")).toBe(true);

  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "new@example.com");
  expect(send.hasAttribute("disabled")).toBe(false);
});

test("a refused invite states the refusal and leaves the address to correct", async () => {
  wire({
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": () =>
      json({ applied: false, message: "taken@example.com is already a member." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const send = await openInvite();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "taken@example.com");
  await userEvent.click(send);

  expect(await screen.findByText("taken@example.com is already a member.")).toBeTruthy();
  expect((screen.getByPlaceholderText("email@work.com") as HTMLInputElement).value).toBe(
    "taken@example.com",
  );
});

test("a member who does not administer the workspace is drawn no invite", async () => {
  wire({});
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  await screen.findByRole("navigation", { name: "Workspace" });
  expect(screen.queryByRole("button", { name: "Invite people" })).toBeNull();
});

test("the invite is its own row under the member's pill, and it opens a dialog", async () => {
  wire({ "/actions/member$": () => json(MEMBER_ACTIONS) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const foot = screen.getByRole("navigation", { name: "Workspace" }).querySelector("footer")!;
  const controls = within(foot)
    .getAllByRole("button")
    .map((entry) => entry.getAttribute("aria-label") ?? entry.textContent);
  expect(controls).toEqual([ADMIN.email, "Invite people"]);

  /* The address is typed in a dialog rather than in a menu: the invite is a piece of work the
     member finishes, and a menu shuts under the first press outside it. */
  await userEvent.click(within(foot).getByRole("button", { name: "Invite people" }));
  const dialog = await screen.findByRole("dialog", { name: "Invite people" });
  expect(within(dialog).getByPlaceholderText("email@work.com")).toBeTruthy();
});
