import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import { AGENT, MEMBER, json, useStreamFake, wire } from "./harness";

const ADMIN = { ...MEMBER, admin: true };

const ROSTER = {
  members: [
    { email: "lead@example.com", admin: true, seated: true },
    { email: "member@example.com", admin: false, seated: false },
  ],
  can_add: true,
  domain: "example.com",
};

beforeEach(() => {
  location.hash = "#/workspace/team";
  useStreamFake();
});

test("the roster names each member, who administers, and who holds a seat", async () => {
  wire({ "/workspace/team": () => json(ROSTER) });
  render(<App agents={[AGENT]} member={ADMIN} />);

  const lead = await screen.findByText("lead@example.com");
  expect(lead.closest("tr")?.textContent).toContain("Admin");
  expect(lead.closest("tr")?.textContent).toContain("Seated");
  const plain = screen.getByText("member@example.com");
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
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.type(await screen.findByPlaceholderText("email@example.com"), "new@example.com");
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Add member" }));

  await waitFor(() => expect(bodies.length).toBe(1));
  expect(JSON.parse(bodies[0])).toEqual({
    verb: "add_member",
    email: "new@example.com",
    admin: true,
  });
  expect(await screen.findByText("Added new@example.com.")).toBeTruthy();
});

test("a refused add states the refusal and leaves the form to correct", async () => {
  wire({
    "/workspace/team": () => json(ROSTER),
    "/intents": () => json({ applied: false, message: "Only an admin adds a member." }),
  });
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.type(await screen.findByPlaceholderText("email@example.com"), "x@example.com");
  await userEvent.click(screen.getByRole("button", { name: "Add member" }));

  expect(await screen.findByText("Only an admin adds a member.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Add member" })).toBeTruthy();
});

test("a member who may not add reads the roster with no form", async () => {
  wire({ "/workspace/team": () => json({ ...ROSTER, can_add: false }) });
  render(<App agents={[AGENT]} member={MEMBER} />);

  expect(await screen.findByText("lead@example.com")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});

test("the placeholder names the workspace domain an address must match", async () => {
  wire({ "/workspace/team": () => json({ ...ROSTER, domain: null }) });
  render(<App agents={[AGENT]} member={ADMIN} />);

  expect(await screen.findByPlaceholderText("email@work.com")).toBeTruthy();
});

test("a roster that fails to read states the error and offers no form", async () => {
  wire({ "/workspace/team": () => new Response("no", { status: 500 }) });
  render(<App agents={[AGENT]} member={ADMIN} />);

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});

test("a read whose body is not json states the failure rather than hanging", async () => {
  wire({ "/workspace/team": () => new Response("<html>", { status: 200 }) });
  render(<App agents={[AGENT]} member={ADMIN} />);

  expect(await screen.findByText("Network error — try again.")).toBeTruthy();
});

test("the sidebar offers Team, and selecting it reads the roster", async () => {
  location.hash = "";
  const { calls } = wire({
    "/workspace/team": () => json(ROSTER),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={ADMIN} />);

  await userEvent.click(await screen.findByRole("button", { name: "Team" }));
  await waitFor(() => expect(calls.some((url) => url.includes("/workspace/team"))).toBe(true));
  expect(await screen.findByText("lead@example.com")).toBeTruthy();
});

test("a failed roster read states the fault through the shared fence", async () => {
  wire({ "/workspace/team": () => new Response("nope", { status: 500 }) });
  render(<App agents={[AGENT]} member={ADMIN} />);

  expect(await screen.findByText("Error 500 — reload to retry.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
});
