import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { agentHash } from "@/lib/route";

import { AGENT, MEMBER, json, useStreamFake, wire } from "./harness";

beforeEach(() => {
  useStreamFake();
});

const SKILLS = [
  { name: "mine", description: "member skill", origin: "member", instructions: "Write tersely." },
  {
    name: "shipped",
    description: "built-in `skill`",
    origin: "deploy",
    instructions: "Read the tree first.",
  },
];

function renderSkills() {
  location.hash = agentHash(AGENT.id, "skills");
  return render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
}

test("the agent skills tab renders cards without a picker or Refresh", async () => {
  wire({ "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();

  expect(await screen.findByText("member skill")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Skills" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  expect(screen.queryByRole("columnheader")).toBeNull();
  expect(document.querySelector('[data-part="mark"]')).toBeNull();
});

test("a card opens the whole skill read-only, and its Delete does not", async () => {
  wire({ "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();

  await userEvent.click(await screen.findByText("member skill"));
  const dialog = await screen.findByRole("dialog");
  expect(dialog.textContent).toContain("mine");
  const instructions = screen.getByLabelText("Instructions") as HTMLTextAreaElement;
  expect(instructions.value).toBe("Write tersely.");
  expect(instructions.readOnly).toBe(true);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("cards sort custom ahead of built-in and render backticks as code", async () => {
  wire({ "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();

  expect(await screen.findByText("member skill")).toBeTruthy();
  const names = [...document.querySelectorAll('[data-part="primary"]')].map(
    (node) => node.textContent,
  );
  expect(names).toEqual(["mine", "shipped"]);
  expect(screen.getByText("skill", { selector: "code" })).toBeTruthy();
});

test("the origin filter labels deploy skills Built-in and hides custom skills", async () => {
  wire({ "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();

  expect(await screen.findByText("member skill")).toBeTruthy();
  expect(screen.getByText("mine").closest("li")?.textContent).toContain("Custom");
  expect(screen.getByText("shipped").closest("li")?.textContent).toContain("Built-in");
  await userEvent.click(screen.getByRole("tab", { name: "Built-in" }));
  expect(screen.queryByText("member skill")).toBeNull();
  expect(screen.getByText("shipped")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
});

test("New skill posts the prepared skill intent for the pane's agent", async () => {
  const posted: unknown[] = [];
  wire({
    "/skills": () => json({ skills: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  renderSkills();

  await userEvent.click(await screen.findByRole("button", { name: "New skill" }));
  await userEvent.type(await screen.findByLabelText("Name"), "fresh");
  await userEvent.type(screen.getByLabelText("Description"), "Load when asked.");
  await userEvent.type(screen.getByLabelText("Instructions"), "body");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "skill",
    name: "fresh",
    spec: { files: { "SKILL.md": "---\nname: fresh\ndescription: \"Load when asked.\"\n---\n\nbody\n" } },
  });
});

test("Delete is offered and posts only for custom skills", async () => {
  const posted: unknown[] = [];
  wire({
    "/skills": () => json({ skills: SKILLS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Deleted." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  renderSkills();

  expect(await screen.findByText("member skill")).toBeTruthy();
  expect(screen.getByText("mine").closest("li")?.querySelector("button")).toBeTruthy();
  expect(screen.getByText("shipped").closest("li")?.querySelector("button")).toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(posted).toEqual([{ verb: "delete", kind: "skill", name: "mine" }]));
});

test("the blank names the agent and holds the only New skill act", async () => {
  wire({ "/skills": () => json({ skills: [] }), "/transcript": () => json({ messages: [] }) });
  renderSkills();

  expect(await screen.findByText("No skill has been saved onto assistant yet.")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "New skill" }).length).toBe(1);
  expect(screen.queryByRole("searchbox")).toBeNull();
});
