import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  destination,
  goTo,
  json,
  refusedNotice,
  useStreamFake,
  wire,
  AGENT,
  MEMBER,
} from "./harness";

/** The add as the member collection projects it: the action's own schema, its label, and the call
 *  template the roster read pre-bound. */
const ADD_MEMBER = {
  name: "add_member",
  description: "Add a member to this workspace ahead of their first contact.",
  input_schema: {
    properties: {
      email: { type: "string", format: "email", title: "Email" },
      admin: { type: "boolean", title: "Admin", default: false },
      notify: { type: "boolean", title: "Notify", default: true },
    },
    required: ["email"],
  },
  call: { kind: "member", action: "add_member", input: {} },
  label: "Add member",
};

/** Open the add: the page's act, then the form it draws in the sheet, whose submit carries the
 *  same label. */
async function openAdd() {
  await userEvent.click(await screen.findByRole("button", { name: "Add member" }));
  const sheet = await screen.findByRole("dialog", { name: "Add member" });
  return within(sheet).getByRole("button", { name: "Add member" });
}

const ADMIN = { ...MEMBER, admin: true };

const ROSTER = {
  members: [
    { email: "lead@example.com", admin: true, seated: true },
    { email: "member@example.com", admin: false, seated: false },
  ],
  can_add: true,
  actions: [ADD_MEMBER],
};

beforeEach(() => {
  location.hash = "#/workspace/team";
  useStreamFake();
});

test("the roster names each member, who administers, and who holds a seat", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const lead = await screen.findByText("lead@example.com");
  expect(lead.closest("tr")?.textContent).toContain("Admin");
  expect(lead.closest("tr")?.textContent).toContain("Seated");
  const plain = within(screen.getByRole("main")).getByText("member@example.com");
  expect(plain.closest("tr")?.textContent).toContain("Member");
  expect(plain.closest("tr")?.textContent).toContain("No seat");
});

test("an admin adds a member by email, optionally as an admin, through the projected action", async () => {
  const posted: { url: string; body: unknown }[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/add_member": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "Added new@example.com." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
  await userEvent.click(screen.getByRole("switch", { name: "Admin" }));
  await userEvent.click(submit);

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    url: "/surface/web/agents/" + AGENT.id + "/actions/member/add_member",
    body: { email: "new@example.com", admin: true, notify: true },
  });
  expect(await screen.findByText("Added new@example.com.")).toBeTruthy();
  expect(screen.queryByRole("dialog", { name: "Add member" })).toBeNull();
});

test("the act stays shut until the address is typed", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  expect(submit.hasAttribute("disabled")).toBe(true);

  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
  expect(submit.hasAttribute("disabled")).toBe(false);
});

test("a search narrows the roster to the members whose address matches", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Search members"), "LEAD{enter}");

  expect(screen.getByText("lead@example.com")).toBeTruthy();
  expect(within(screen.getByRole("main")).queryByText("member@example.com")).toBeNull();
});

test("a search that matches nobody says so in a row, and the table holds", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Search members"), "nobody{enter}");

  expect(screen.getByText("No member matches this search.")).toBeTruthy();
  expect(screen.getByRole("table")).toBeTruthy();
  expect(screen.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Member",
    "Role",
    "Seat",
  ]);
});

test("a refused add states the refusal and leaves the form to correct", async () => {
  wire({
    "/workspace/team": () => json(ROSTER),
    "/add_member": () => json({ applied: false, message: "Only an admin adds a member." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "x@example.com");
  await userEvent.click(submit);

  await refusedNotice("Only an admin adds a member.");
  expect(screen.getByRole("dialog", { name: "Add member" })).toBeTruthy();
  expect((screen.getByRole("textbox", { name: "Email" }) as HTMLInputElement).value).toBe(
    "x@example.com",
  );
});

test("the tab names the roster, so the section under it repeats no heading", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(destination()).toBe("Team");
  expect(screen.queryByRole("heading", { name: "Members" })).toBeNull();
  await openAdd();
  expect(screen.getByRole("dialog", { name: "Add member" })).toBeTruthy();
});

test("the add form overlays the roster in the shared sheet", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();

  const form = screen.getByRole("dialog", { name: "Add member" });
  expect(within(form).getByRole("textbox", { name: "Email" })).toBeTruthy();
  expect(screen.getByText("lead@example.com")).toBeTruthy();
  expect(location.hash).toBe("#/workspace/team");
});

test("the address is an email field, and the defaults the action declares stand", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();
  expect(screen.getByRole("textbox", { name: "Email" }).getAttribute("type")).toBe("email");
  expect((screen.getByRole("switch", { name: "Admin" }) as HTMLInputElement).checked).toBe(false);
  expect((screen.getByRole("switch", { name: "Notify" }) as HTMLInputElement).checked).toBe(true);
});

test("an add already in flight is not sent twice", async () => {
  let sends = 0;
  wire({
    "/workspace/team": () => json(ROSTER),
    "/add_member": () => {
      sends += 1;
      return new Promise(() => undefined) as unknown as Response;
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
  await userEvent.click(submit);
  await waitFor(() => expect(submit.getAttribute("aria-disabled")).toBe("true"));
  await userEvent.click(submit);

  expect(sends).toBe(1);
  expect(document.activeElement).toBe(submit);
});

test("a member who may not add reads the roster with no form", async () => {
  wire({ "/workspace/team": () => json({ ...ROSTER, can_add: false }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("lead@example.com")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});

test("a roster that fails to read states the error and offers no form", async () => {
  wire({ "/workspace/team": () => new Response("no", { status: 500 }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});

test("a read whose body is not json states the failure rather than hanging", async () => {
  wire({ "/workspace/team": () => new Response("<html>", { status: 200 }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
});

test("the sidebar offers the workspace, which opens on the roster", async () => {
  location.hash = "";
  const { calls } = wire({
    "/workspace/team": () => json(ROSTER),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
  await waitFor(() => expect(calls.some((url) => url.includes("/workspace/team"))).toBe(true));
  expect(await screen.findByText("lead@example.com")).toBeTruthy();
});

test("an outcome notice does not follow the member to another view", async () => {
  wire({
    "/workspace/team": () => json(ROSTER),
    "/workspace/credentials": () =>
      json({
        slots: [
          {
            name: "openai",
            slot: "OPENAI_API_KEY",
            extension: "models",
            description: "the key",
            filled: true,
          },
        ],
        actions: [],
      }),
    "/transcript": () => json({ messages: [] }),
    "/add_member": () => json({ applied: true, message: "Added member@example.com." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
  const submit = await openAdd();
  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
  await userEvent.click(submit);
  expect(await screen.findByText("Added member@example.com.")).toBeTruthy();

  await goTo("Credentials");

  expect(await screen.findByText("OPENAI_API_KEY")).toBeTruthy();
  expect(screen.queryByText("Added member@example.com.")).toBeNull();

  await goTo("Team");

  expect(await screen.findByText("lead@example.com")).toBeTruthy();
  expect(screen.queryByText("Added member@example.com.")).toBeNull();
});

test("a failed roster read states the fault through the shared fence", async () => {
  wire({ "/workspace/team": () => new Response("nope", { status: 500 }) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});
