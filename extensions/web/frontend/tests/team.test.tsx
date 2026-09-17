import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  destination,
  goTo,
  json,
  refusedNotice,
  pageFits,
  tableFloors,
  useStreamFake,
  wire,
  AGENT,
  MEMBER,
} from "./harness";

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

async function openAdd() {
  await userEvent.click(await screen.findByRole("button", { name: "Add member" }));
  const sheet = await screen.findByRole("dialog", { name: "Add member" });
  return within(sheet).getByRole("button", { name: "Add member" });
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
  actions: [ADD_MEMBER],
};

const DISABLED_LEAD = {
  ...ROSTER,
  members: [{ ...ROSTER.members[0], seated: false }, ROSTER.members[1]],
};

const FLOOR =
  "calc(1 * var(--size-fact-column) + 0 * var(--size-stamp-column) + 2 * var(--size-prose-column) + 1 * var(--size-act))";

beforeEach(() => {
  location.hash = "#/workspace/team";
  useStreamFake();
});

test("the roster names each member, who administers, and whose access is live", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const lead = await screen.findByText("lead@example.com");
  expect(lead.closest("tr")?.textContent).toContain("Admin");
  expect(within(lead.closest("tr") as HTMLElement).queryByRole("img", { name: "Disabled" })).toBeNull();
  const plain = within(screen.getByRole("main")).getByText("member@example.com");
  expect(plain.closest("tr")?.textContent).toContain("Member");
  expect(within(plain.closest("tr") as HTMLElement).getByRole("img", { name: "Disabled" })).toBeTruthy();
});

test("a disabled member sinks under the seated ones and their row recedes", async () => {
  wire({
    "/workspace/team": () =>
      json({
        ...ROSTER,
        members: [
          { ...ROSTER.members[0], seated: false },
          { ...ROSTER.members[1], seated: true },
        ],
      }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("lead@example.com");
  const rows = within(screen.getByRole("main")).getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getByText(/@example\.com$/).textContent)).toEqual([
    "member@example.com",
    "lead@example.com",
  ]);
  expect(rowOf("lead@example.com").querySelector("td")?.className).toContain(
    "opacity-(--opacity-muted-strong)",
  );
  expect(rowOf("member@example.com").querySelector("td")?.className).not.toContain(
    "opacity-(--opacity-muted-strong)",
  );
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

test("Add member stands on the band the search is on, not on the page's name", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const add = await screen.findByRole("button", { name: "Add member" });
  const band = add.closest("div")?.parentElement as HTMLElement;
  expect(within(band).getByLabelText("Search members")).toBeTruthy();
  const header = document.querySelector<HTMLElement>('[data-slot="header"]')!;
  expect(header.contains(add)).toBe(false);
});

test("a search that matches nobody says so in a row, and the table holds", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.type(await screen.findByLabelText("Search members"), "nobody{enter}");

  expect(screen.getByText("No member matches this search.")).toBeTruthy();
  expect(screen.getByRole("table")).toBeTruthy();
  expect(screen.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Member",
    "Email",
    "Role",
    "",
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

test("a member who administers nothing reads the roster and every act is absent", async () => {
  wire({ "/workspace/team": () => json({ ...ROSTER, can_manage: false }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("lead@example.com")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Actions for / })).toBeNull();
  expect(screen.queryByRole("button", { name: "Disable" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Make admin" })).toBeNull();
  expect(screen.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Member",
    "Email",
    "Role",
  ]);
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

test("a credential's provider mark carries no round mask", async () => {
  location.hash = "#/workspace/credentials";
  wire({
    "/workspace/credentials": () =>
      json({
        slots: [
          {
            name: "openai",
            slot: "OPENAI_API_KEY",
            extension: "models",
            description: "the key",
            filled: true,
            entries: [],
          },
        ],
        actions: [],
      }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const row = (await screen.findByText("OPENAI_API_KEY")).closest("[data-slot=item]")!;
  const mark = row.querySelector<HTMLElement>('[style*="--brand-openai"]');
  expect(mark).not.toBeNull();
  expect(mark!.className).not.toContain("rounded-full");
});

test("an outcome notice does not follow the member to another view", async () => {
  wire({
    "/workspace/team": () => json(ROSTER),
    "/workspace/memory": () =>
      json({ available: true, actions: [], kinds: ["fact", "profile"], matches: [] }),
    "/transcript": () => json({ messages: [] }),
    "/add_member": () => json({ applied: true, message: "Added member@example.com." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await userEvent.click(await screen.findByRole("button", { name: "Workspace" }));
  const submit = await openAdd();
  await userEvent.type(screen.getByRole("textbox", { name: "Email" }), "new@example.com");
  await userEvent.click(submit);
  expect(await screen.findByText("Added member@example.com.")).toBeTruthy();

  await goTo("Memory");

  expect(await screen.findByText("No memories yet.")).toBeTruthy();
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

async function pickAct(row: HTMLElement, verb: string) {
  await userEvent.click(within(row).getByRole("button", { name: /^Actions for / }));
  await userEvent.click(await screen.findByRole("menuitem", { name: verb }));
}

async function confirmed(row: HTMLElement, verb: string) {
  await pickAct(row, verb);
  const asking = await screen.findByRole("dialog");
  await userEvent.click(within(asking).getByRole("button", { name: verb }));
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
  await waitFor(() =>
    expect(within(rowOf("lead@example.com")).getByRole("img", { name: "Disabled" })).toBeTruthy(),
  );
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
  await pickAct(rowOf("member@example.com"), "Enable");

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
  await pickAct(rowOf("member@example.com"), "Make admin");

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "apply",
    kind: "member",
    name: PLAIN_ID,
    spec: { admin: true, seated: false },
  });
});

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
  expect(
    within(rowOf("lead@example.com")).queryByRole("img", { name: "Disabled" }),
  ).toBeNull();
});

test("the roster's widest table fits the desktop page it is read on", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  await screen.findByText("lead@example.com");
  expect(tableFloors()).toEqual([FLOOR]);
  expect(pageFits(FLOOR)).toBe(true);
});
