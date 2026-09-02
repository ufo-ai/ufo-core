import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  destination,
  goTo,
  json,
  pick,
  refusedNotice,
  pageFits,
  tableFloors,
  useStreamFake,
  wire,
  AGENT,
  MEMBER,
} from "./harness";

async function openAdd() {
  await userEvent.click(await screen.findByRole("button", { name: "Add member" }));
  return screen.getByRole("button", { name: "Add members" });
}

const ADMIN = { ...MEMBER, admin: true };

const LEAD_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const PLAIN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const ROSTER = {
  members: [
    { id: LEAD_ID, email: "lead@example.com", admin: true, seated: true },
    { id: PLAIN_ID, email: "member@example.com", admin: false, seated: false },
  ],
  can_manage: true,
};

/** The same roster once the lead's access is off — what the re-read answers, so a landed apply is
 *  read back off the roster rather than assumed by the row that sent it. */
const DISABLED_LEAD = {
  ...ROSTER,
  members: [{ ...ROSTER.members[0], seated: false }, ROSTER.members[1]],
};

/** What the roster's tracks sum to: the role and the status on fact tracks, the address and the
 *  acts cell beside it on shared ones. */
const FLOOR =
  "calc(2 * var(--size-fact-column) + 2 * var(--size-prose-column) + 0 * var(--size-act))";

beforeEach(() => {
  location.hash = "#/workspace/team";
  useStreamFake();
});

test("the roster names each member, who administers, and whose access is live", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const lead = await screen.findByText("lead@example.com");
  expect(lead.closest("tr")?.textContent).toContain("Admin");
  expect(lead.closest("tr")?.textContent).toContain("Active");
  const plain = within(screen.getByRole("main")).getByText("member@example.com");
  expect(plain.closest("tr")?.textContent).toContain("Member");
  expect(plain.closest("tr")?.textContent).toContain("Disabled");
});

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

test("an admin adds a member by email, optionally as an admin, through the action lane", async () => {
  const bodies: string[] = [];
  const urls: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": (url, init) => {
      urls.push(url);
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Added new@example.com." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "new@example.com");
  await pick("Role", "Admin");
  await userEvent.click(submit);

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(urls[0]).toContain("/agents/" + AGENT.id + "/actions/member/add_member");
  expect(JSON.parse(bodies[0])).toEqual({
    email: "new@example.com",
    admin: true,
  });
  expect(await screen.findByText("Added new@example.com.")).toBeTruthy();
});

test("the address and its role stretch to one height, so the chevron sits on the row's line", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();
  const row = screen.getByLabelText("Email").parentElement;
  expect(row).toBe(screen.getByLabelText("Role").parentElement);
  expect(row?.className).toContain("items-stretch");
});

test("the act stays shut until every row carries an address", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  expect(submit.hasAttribute("disabled")).toBe(true);

  await userEvent.type(screen.getByLabelText("Email"), "new@example.com");
  expect(submit.hasAttribute("disabled")).toBe(false);

  await userEvent.click(screen.getByRole("button", { name: "Add more" }));
  expect(submit.hasAttribute("disabled")).toBe(true);
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
    "Status",
    "",
  ]);
});

test("added rows each send their own act, and the roster reports the count", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "Added someone." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.click(screen.getByRole("button", { name: "Add more" }));
  await userEvent.type(screen.getByLabelText("Email 1"), "one@example.com");
  await userEvent.type(screen.getByLabelText("Email 2"), "two@example.com");
  await pick("Role 2", "Admin");
  await userEvent.click(submit);

  await waitFor(() => expect(bodies.length).toBe(2));
  expect(JSON.parse(bodies[0])).toEqual({
    email: "one@example.com",
    admin: false,
  });
  expect(JSON.parse(bodies[1])).toEqual({
    email: "two@example.com",
    admin: true,
  });
  expect(await screen.findByText("2 members added.")).toBeTruthy();
});

test("only the refused rows stay behind, so a second press cannot re-add what landed", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": (_url, init) => {
      const body = String(init?.body);
      bodies.push(body);
      return body.includes("bad@example.com")
        ? json({ applied: false, message: "bad@example.com is already a member." })
        : json({ applied: true, message: "Added good@example.com." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.click(screen.getByRole("button", { name: "Add more" }));
  await userEvent.type(screen.getByLabelText("Email 1"), "good@example.com");
  await userEvent.type(screen.getByLabelText("Email 2"), "bad@example.com");
  await userEvent.click(submit);

  await refusedNotice("bad@example.com is already a member.");
  expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("bad@example.com");

  await userEvent.click(submit);
  await waitFor(() => expect(bodies.length).toBe(3));
  expect(bodies.filter((body) => body.includes("good@example.com")).length).toBe(1);
});

test("a refused add states the refusal and leaves the form to correct", async () => {
  wire({
    "/workspace/team": () => json(ROSTER),
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": () => json({ applied: false, message: "Only an admin adds a member." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "x@example.com");
  await userEvent.click(submit);

  await refusedNotice("Only an admin adds a member.");
  expect(screen.getByRole("dialog", { name: "Add members" })).toBeTruthy();
  expect((screen.getByPlaceholderText("email@work.com") as HTMLInputElement).value).toBe(
    "x@example.com",
  );
});

test("the tab names the roster, so the section under it repeats no heading", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  expect(destination()).toBe("Team");
  expect(screen.queryByRole("heading", { name: "Members" })).toBeNull();
  await openAdd();
  expect(screen.getByRole("dialog", { name: "Add members" })).toBeTruthy();
});

test("the add form overlays the roster in the shared sheet", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();

  const form = screen.getByRole("dialog", { name: "Add members" });
  expect(within(form).getByPlaceholderText("email@work.com")).toBeTruthy();
  expect(screen.getByText("lead@example.com")).toBeTruthy();
  expect(location.hash).toBe("#/workspace/team");
});

/** The panel takes focus as it lands, so a member who cannot see it is told its name and nothing
 *  else. What it does with every address typed into it is one line the panel already draws, and a
 *  panel that names that line as its description says it to that member as they arrive. */
test("the add panel states what it does to the member focus lands on", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();

  const panel = screen.getByRole("dialog", { name: "Add members" });
  const describes = panel.getAttribute("aria-describedby");
  expect(describes).toBeTruthy();
  expect(document.getElementById(String(describes))?.textContent).toBe(
    "Each address is added to this workspace, at any email domain.",
  );
});

test("the address and role are named for a member who cannot see the placeholder", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();
  expect(screen.getByLabelText("Email").getAttribute("type")).toBe("email");
  expect(screen.getByRole("combobox", { name: "Role" }).textContent).toBe("Member");
});

test("an add already in flight is not sent twice", async () => {
  let sends = 0;
  wire({
    "/workspace/team": () => json(ROSTER),
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": () => {
      sends += 1;
      return new Promise(() => undefined) as unknown as Response;
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "new@example.com");
  await userEvent.click(submit);
  await waitFor(() => expect(submit.getAttribute("aria-disabled")).toBe("true"));
  await userEvent.click(submit);

  expect(sends).toBe(1);
  expect(document.activeElement).toBe(submit);
});

test("a member who administers nothing reads the roster and every act is absent", async () => {
  wire({ "/workspace/team": () => json({ ...ROSTER, can_manage: false }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("lead@example.com")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Make admin" })).toBeNull();
  expect(screen.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Member",
    "Role",
    "Status",
  ]);
});

test("the form admits an address at any domain and hints a neutral one", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await openAdd();
  expect(
    screen.getByText("Each address is added to this workspace, at any email domain."),
  ).toBeTruthy();
  expect(screen.getByPlaceholderText("email@work.com")).toBeTruthy();
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
      }),
    "/transcript": () => json({ messages: [] }),
    "/actions/member$": () => json(MEMBER_ACTIONS),
    "/actions/member/add_member": () => json({ applied: true, message: "Added member@example.com." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
  const submit = await openAdd();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "new@example.com");
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

/** The confirm arms on the first press and fires on the second, so a disable is two presses of the
 *  one control. */
async function confirmed(row: HTMLElement, verb: string) {
  const act = within(row).getByRole("button", { name: verb });
  await userEvent.click(act);
  await userEvent.click(within(row).getByRole("button", { name: "Confirm " + verb.toLowerCase() }));
}

function rowOf(email: string): HTMLElement {
  return within(screen.getByRole("main")).getByText(email).closest("tr") as HTMLElement;
}

test("an admin disables a member's access, and the whole spec rides the apply", async () => {
  const bodies: string[] = [];
  const urls: string[] = [];
  let reads = 0;
  wire({
    "/workspace/team": () => {
      reads += 1;
      return json(reads === 1 ? ROSTER : DISABLED_LEAD);
    },
    "/intents": (url, init) => {
      urls.push(url);
      bodies.push(String(init?.body));
      return json({ applied: true, message: "lead@example.com is disabled." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("lead@example.com");
  await confirmed(rowOf("lead@example.com"), "Disable");

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(urls[0]).toContain("/agents/" + AGENT.id + "/intents");
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "apply",
    kind: "member",
    name: LEAD_ID,
    spec: { admin: true, seated: false },
  });
  expect(await screen.findByText("lead@example.com is disabled.")).toBeTruthy();
  await waitFor(() => expect(rowOf("lead@example.com").textContent).toContain("Disabled"));
});

test("an admin enables a disabled member, and the role rides unchanged", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "member@example.com is active." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("member@example.com");
  await userEvent.click(within(rowOf("member@example.com")).getByRole("button", { name: "Enable" }));

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "apply",
    kind: "member",
    name: PLAIN_ID,
    spec: { admin: false, seated: true },
  });
});

test("an admin promotes a member, and the access state rides unchanged", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": (_url, init) => {
      bodies.push(String(init?.body));
      return json({ applied: true, message: "member@example.com administers this workspace." });
    },
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("member@example.com");
  await userEvent.click(
    within(rowOf("member@example.com")).getByRole("button", { name: "Make admin" }),
  );

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "apply",
    kind: "member",
    name: PLAIN_ID,
    spec: { admin: true, seated: false },
  });
});

/** The kind's own guards are what refuse, so the roster states the refusal rather than quietly
 *  leaving the row as it was — the last active admin disabling themselves is the case, and a screen
 *  that said nothing would read as a control that does nothing. */
test("a refused apply states the refusal and leaves the row standing", async () => {
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": () =>
      json({ applied: false, message: "The last seated admin cannot be unseated." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("lead@example.com");
  await confirmed(rowOf("lead@example.com"), "Disable");

  await refusedNotice("The last seated admin cannot be unseated.");
  expect(rowOf("lead@example.com").textContent).toContain("Active");
});

/** The tracks are fixed pixels, so a table declaring more of them than the desktop leaves holds its
 *  width and the column scrolls sideways — and what falls off the right is the act the row is
 *  pressed by. The roster is the widest shape it draws: three facts and the acts. */
test("the roster's widest table fits the desktop page it is read on", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("lead@example.com");
  expect(tableFloors()).toEqual([FLOOR]);
  expect(pageFits(FLOOR)).toBe(true);
});
