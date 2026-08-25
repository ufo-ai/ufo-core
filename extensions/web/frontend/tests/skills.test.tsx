import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";

import {
  AGENT,
  MEMBER,
  SETTINGS,
  destination,
  json,
  openAgentSettings,
  useStreamFake,
  wire,
} from "./harness";

beforeEach(() => {
  location.hash = "#/workspace/skills";
  useStreamFake();
});

const SKILLS = [
  {
    name: "mine",
    description: "member skill",
    origin: "member",
    instructions: "Write tersely.",
    depends: ["sandbox"],
    agents: ["helpdesk"],
  },
  {
    name: "shipped",
    description: "built-in `skill`",
    origin: "deploy",
    instructions: "Read the tree first.",
    depends: [],
    agents: [],
  },
];

const MINE_GENERATION = "11111111-2222-4333-8444-555555555555";

const MINE_DETAIL = {
  spec: {
    files: {
      "SKILL.md": { sha256: "a".repeat(64) },
      "checklist.md": { sha256: "b1946ac92492d2347c6235b4d2611184" },
    },
    pinned: true,
  },
  generation: MINE_GENERATION,
};

const NO_COMMUNITY = { "/skills/community": () => json({ skills: [] }) };
const MINE_OBJECT = { "/objects/skill/mine": () => json(MINE_DETAIL) };

/** The skills stand on the workspace page, which its own address opens. */
function renderSkills() {
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

/** The page opens on the directory, so every read of the workspace's own skills starts with the
 *  pick that names the other collection. */
async function openInstalled() {
  await userEvent.click(await screen.findByRole("tab", { name: "Installed" }));
}

test("the workspace skills page renders items as a list, without a picker or a table's chrome", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  expect(destination()).toBe("Skills");
  const panel = within(screen.getByRole("main"));
  expect(panel.queryByRole("combobox", { name: "App" })).toBeNull();
  expect(panel.queryByRole("button", { name: "Refresh" })).toBeNull();
  expect(panel.queryByRole("columnheader")).toBeNull();
  expect(document.querySelector('[data-part="mark"]')).toBeNull();
  const group = screen.getByText("mine").closest("ul") as HTMLElement;
  expect(group.querySelectorAll("li[aria-hidden]").length).toBe(1);
});

test("the app's settings dialog holds no skills tab", async () => {
  location.hash = "#/agents/" + AGENT.id;
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = within(await openAgentSettings());

  expect(dialog.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
    "Settings",
    "Connectors",
    "Scheduled",
  ]);
});

test("the settings form states the workspace-skill setting and saves it", async () => {
  const posted: unknown[] = [];
  location.hash = "#/agents/" + AGENT.id;
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Applied." });
    },
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  await openAgentSettings();

  const checkbox = (await screen.findByLabelText("Use workspace skills")) as HTMLInputElement;
  expect(checkbox.checked).toBe(true);
  await userEvent.click(checkbox);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { use_workspace_skills: false },
  });
});

test("a custom skill opens as an editable record, and a built-in one read-only", async () => {
  wire({ ...NO_COMMUNITY, ...MINE_OBJECT, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  await userEvent.click(await screen.findByText("shipped"));
  const dialog = await screen.findByRole("dialog", { name: "shipped" });
  const shipped = within(dialog).getByLabelText("Instructions") as HTMLTextAreaElement;
  expect(shipped.value).toBe("Read the tree first.");
  expect(shipped.readOnly).toBe(true);
  expect(within(dialog).queryByRole("button", { name: "Save" })).toBeNull();
  await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));

  await userEvent.click(screen.getByText("mine"));
  const form = await screen.findByRole("dialog", { name: "mine" });
  expect(within(form).getByLabelText("Description")).toHaveProperty("value", "member skill");
  expect((within(form).getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
    "Write tersely.",
  );
  expect((within(form).getByLabelText("Name") as HTMLInputElement).readOnly).toBe(true);
  expect(within(form).getAllByRole("heading", { name: "mine" }).length).toBe(1);
  expect(within(form).getAllByRole("button", { name: "Close" }).length).toBe(1);
});

test("items sort custom ahead of built-in and state where each came from", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  const names = [...document.querySelectorAll('[data-part="primary"]')].map(
    (node) => node.textContent,
  );
  expect(names).toEqual(["mine", "shipped"]);
  expect(screen.getByText("mine").closest("li")?.textContent).toContain("Custom");
  expect(screen.getByText("shipped").closest("li")?.textContent).toContain("Built-in");
  expect(within(screen.getByRole("main")).getByRole("tab", { name: "Community" })).toBeTruthy();
});

test("New skill posts the prepared skill intent for the main agent", async () => {
  const posted: { url: string; body: unknown }[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [] }),
    "/intents": (url, init) => {
      posted.push({ url, body: JSON.parse(String(init?.body)) });
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();

  await userEvent.click(await screen.findByRole("button", { name: "New skill" }));
  await userEvent.type(await screen.findByLabelText("Name"), "fresh");
  await userEvent.type(screen.getByLabelText("Description"), "Load when asked.");
  await userEvent.type(screen.getByLabelText("Instructions"), "body");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0].url).toContain(AGENT.id);
  expect(posted[0].body).toEqual({
    verb: "apply",
    kind: "skill",
    name: "fresh",
    spec: {
      files: { "SKILL.md": '---\nname: fresh\ndescription: "Load when asked."\n---\n\nbody\n' },
      pinned: false,
    },
  });
});

test("an edit carries the generation, the digests, the frontmatter metadata, and the pin", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    ...MINE_OBJECT,
    "/skills": () => json({ skills: SKILLS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();
  await openInstalled();

  await userEvent.click(await screen.findByText("mine"));
  const instructions = await screen.findByLabelText("Instructions");
  await userEvent.clear(instructions);
  await userEvent.type(instructions, "Write plainly.");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "skill",
    name: "mine",
    spec: {
      files: {
        "SKILL.md":
          '---\nname: mine\ndescription: "member skill"\nmetadata:\n  depends: ["sandbox"]\n' +
          '  agents: ["helpdesk"]\n---\n\nWrite plainly.\n',
        "checklist.md": { sha256: "b1946ac92492d2347c6235b4d2611184" },
      },
      pinned: true,
    },
    generation: MINE_GENERATION,
  });
});

test("a save over someone else's newer save surfaces the refusal", async () => {
  wire({
    ...NO_COMMUNITY,
    ...MINE_OBJECT,
    "/skills": () => json({ skills: SKILLS }),
    "/intents": () =>
      json({ applied: false, message: "skill 'mine' changed after it was read." }),
  });
  renderSkills();
  await openInstalled();

  await userEvent.click(await screen.findByText("mine"));
  await userEvent.click(await screen.findByRole("button", { name: "Save" }));

  expect(await screen.findByText("skill 'mine' changed after it was read.")).toBeTruthy();
});

test("Delete ends the skill through the intent lane", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    ...MINE_OBJECT,
    "/skills": () => json({ skills: SKILLS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Deleted." });
    },
  });
  renderSkills();
  await openInstalled();

  await userEvent.click(await screen.findByText("mine"));
  await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm delete" }));

  await waitFor(() => expect(posted).toEqual([{ verb: "delete", kind: "skill", name: "mine" }]));
  expect(await screen.findByText("Deleted.")).toBeTruthy();
});

test("the blank names the workspace, and the header holds the only New skill act", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: [] }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("No skill has been saved onto this workspace yet.")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "New skill" }).length).toBe(1);
});

test("the page's search narrows the skills the workspace holds", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("shipped")).toBeTruthy();
  await userEvent.type(await screen.findByLabelText("Search skills"), "mine{enter}");

  expect(await screen.findByText("mine")).toBeTruthy();
  expect(screen.queryByText("shipped")).toBeNull();
});

test("the writing form opens in the shared sheet, and the address carries no half-written skill", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();

  await userEvent.click(await screen.findByRole("button", { name: "New skill" }));

  const form = await screen.findByRole("dialog", { name: "New skill" });
  expect(within(form).getByLabelText("Name")).toBeTruthy();
  expect(within(form).getAllByRole("heading", { name: "New skill" }).length).toBe(1);
  expect(location.hash).toBe("#/workspace/skills");
});
