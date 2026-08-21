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

const ROSTER = {
  members: [
    { email: "lead@example.com", admin: true, seated: true },
    { email: "member@example.com", admin: false, seated: false },
  ],
  can_add: true,
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

test("an admin adds a member by email, optionally as an admin, through the intent lane", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": (_url, init) => {
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
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "add_member",
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
    "Seat",
  ]);
});

test("added rows each send their own intent, and the roster reports the count", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": (_url, init) => {
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
    verb: "add_member",
    email: "one@example.com",
    admin: false,
  });
  expect(JSON.parse(bodies[1])).toEqual({
    verb: "add_member",
    email: "two@example.com",
    admin: true,
  });
  expect(await screen.findByText("2 members added.")).toBeTruthy();
});

test("only the refused rows stay behind, so a second press cannot re-add what landed", async () => {
  const bodies: string[] = [];
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": (_url, init) => {
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
    "/intents": () => json({ applied: false, message: "Only an admin adds a member." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} onAgents={() => {}} />);

  const submit = await openAdd();
  await userEvent.type(screen.getByPlaceholderText("email@work.com"), "x@example.com");
  await userEvent.click(submit);

  await refusedNotice("Only an admin adds a member.");
  expect(screen.getByRole("complementary", { name: "Add members" })).toBeTruthy();
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
  expect(screen.getByRole("complementary", { name: "Add members" })).toBeTruthy();
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
    "/intents": () => {
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

test("a member who may not add reads the roster with no form", async () => {
  wire({ "/workspace/team": () => json({ ...ROSTER, can_add: false }) });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);

  expect(await screen.findByText("lead@example.com")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
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
    "/intents": () => json({ applied: true, message: "Added member@example.com." }),
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
