import { render, screen, waitFor, within } from "@testing-library/react";
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

const NO_COMMUNITY = { "/skills/community": () => json({ skills: [] }) };

function renderSkills() {
  location.hash = agentHash(AGENT.id, "skills");
  return render(<App agents={[AGENT]} subagents={[]} member={MEMBER} newAgent={null} onAgents={() => {}} />);
}

/** The tab opens on the directory, so every read of the agent's own skills starts with the pick
 *  that names the other collection. */
async function openInstalled() {
  await userEvent.click(await screen.findByRole("tab", { name: "Installed" }));
}

test("the agent skills tab renders items without a picker or Refresh", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Skills" }).getAttribute("aria-selected")).toBe("true");
  const panel = within(screen.getByTestId("panel"));
  expect(panel.queryByRole("combobox", { name: "Agent" })).toBeNull();
  expect(panel.queryByRole("button", { name: "Refresh" })).toBeNull();
  expect(panel.queryByRole("columnheader")).toBeNull();
  expect(document.querySelector('[data-part="mark"]')).toBeNull();
  const group = screen.getByText("mine").closest("ul") as HTMLElement;
  expect(group.querySelectorAll("li[aria-hidden]").length).toBe(1);
});

test("an item opens the whole skill read-only, and states its description there", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();
  await openInstalled();

  expect(screen.queryByText("member skill")).toBeNull();
  await userEvent.click(await screen.findByText("mine"));
  const dialog = await screen.findByRole("dialog", { name: "mine" });
  expect(dialog.textContent).toContain("mine");
  expect(screen.getByLabelText("Description")).toHaveProperty("value", "member skill");
  const instructions = screen.getByLabelText("Instructions") as HTMLTextAreaElement;
  expect(instructions.value).toBe("Write tersely.");
  expect(instructions.readOnly).toBe(true);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog", { name: "mine" })).toBeNull();
});

test("items sort custom ahead of built-in", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  const names = [...document.querySelectorAll('[data-part="primary"]')].map(
    (node) => node.textContent,
  );
  expect(names).toEqual(["mine", "shipped"]);
});

test("an item states where its skill came from, and the filter names collections", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }), "/transcript": () => json({ messages: [] }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  expect(screen.getByText("mine").closest("li")?.textContent).toContain("Custom");
  expect(screen.getByText("shipped").closest("li")?.textContent).toContain("Built-in");
  const tabs = within(screen.getByTestId("panel"));
  expect(tabs.queryByRole("tab", { name: "Custom" })).toBeNull();
  expect(tabs.queryByRole("tab", { name: "Built-in" })).toBeNull();
  expect(tabs.queryByRole("tab", { name: "All" })).toBeNull();
  expect(tabs.getByRole("tab", { name: "Community" })).toBeTruthy();
});

test("New skill posts the prepared skill intent for the pane's agent", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
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

test("Delete stands in the skill's own dialog, and only for a custom skill", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: SKILLS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Deleted." });
    },
    "/transcript": () => json({ messages: [] }),
  });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  const state = within(screen.getByText("mine").closest("li") as HTMLElement).getByRole("button");
  expect(state.textContent).toBe("Installed");
  expect((state as HTMLButtonElement).disabled).toBe(true);

  await userEvent.click(screen.getByText("shipped"));
  const shipped = await screen.findByRole("dialog", { name: "shipped" });
  expect(within(shipped).queryByRole("button", { name: "Delete" })).toBeNull();
  await userEvent.click(within(shipped).getByRole("button", { name: "Close" }));

  await userEvent.click(screen.getByText("mine"));
  const dialog = await screen.findByRole("dialog", { name: "mine" });
  expect(within(dialog).getByLabelText("Description").tagName).toBe("TEXTAREA");
  expect([...dialog.querySelectorAll("button")].map((one) => one.textContent)).toEqual([
    "Delete",
    "Close",
  ]);
  await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
  await userEvent.click(within(dialog).getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(posted).toEqual([{ verb: "delete", kind: "skill", name: "mine" }]));
  expect(screen.queryByRole("dialog", { name: "mine" })).toBeNull();
});

test("the blank names the agent, and the bar holds the only New skill act", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: [] }), "/transcript": () => json({ messages: [] }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("No skill has been saved onto assistant yet.")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "New skill" }).length).toBe(1);
});
