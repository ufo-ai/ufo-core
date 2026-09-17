import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test } from "vitest";

import { App } from "@/App";
import { newChatHash } from "@/lib/route";

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

function pickFile(name: string, body: string) {
  const input = screen.getByTestId("skill-upload") as HTMLInputElement;
  Object.defineProperty(input, "files", {
    configurable: true,
    value: [new File([body], name, { type: "text/markdown" })],
  });
  fireEvent.change(input);
}

function renderSkills() {
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
}

async function openInstalled() {
  await userEvent.click(await screen.findByRole("tab", { name: "Installed" }));
}

test("the workspace skills page renders its records as a table, without a picker", async () => {
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [...SKILLS, { ...SKILLS[0], name: "second" }] }),
  });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  expect(destination()).toBe("Skills");
  const panel = within(screen.getByRole("main"));
  expect(panel.queryByRole("combobox", { name: "App" })).toBeNull();
  expect(panel.queryByRole("button", { name: "Refresh" })).toBeNull();
  expect(panel.getAllByRole("columnheader").map((head) => head.textContent)).toEqual([
    "Skill",
    "Instructions",
    "",
  ]);
  expect(
    panel
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[0]?.textContent),
  ).toEqual(["mine", "second"]);
});

test("the app's own panel offers no skills read", async () => {
  location.hash = "#/agents/" + AGENT.id;
  wire({
    "/settings": () => json(SETTINGS),
    "/connections": () => json({ connections: [] }),
    "/transcript": () => json({ messages: [] }),
  });
  render(<App agents={[AGENT]} member={MEMBER} onAgents={() => {}} />);
  const dialog = within(await openAgentSettings());

  await userEvent.click(dialog.getByRole("button", { name: "Settings" }));
  expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
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

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toMatchObject({
    verb: "apply",
    kind: "agent",
    name: "assistant",
    spec: { use_workspace_skills: false },
  });
});

test("a custom skill opens as an editable record", async () => {
  wire({ ...NO_COMMUNITY, ...MINE_OBJECT, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  await userEvent.click(await screen.findByText("mine"));
  const form = await screen.findByRole("dialog", { name: "mine" });
  expect(within(form).getByLabelText("Description")).toHaveProperty("value", "member skill");
  expect((within(form).getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe(
    "Write tersely.",
  );
  expect((within(form).getByLabelText("Name") as HTMLInputElement).readOnly).toBe(true);
  expect(within(form).getAllByRole("heading", { name: "mine" }).length).toBe(1);
  expect(within(form).getAllByRole("button", { name: "Close" }).length).toBe(1);
});

test("the list holds the member's skills and leaves the built-in ones out", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("mine")).toBeTruthy();
  const rows = within(screen.getByRole("main")).getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getAllByRole("cell")[0]?.textContent)).toEqual(["mine"]);
  expect(within(rows[0]).getAllByRole("cell")[1]?.textContent).toBe("Write tersely.");
  expect(screen.queryByText("member skill")).toBeNull();
  expect(within(screen.getByRole("main")).getByRole("tab", { name: "Community" })).toBeTruthy();
});

test("the preview closes up the workflow's line breaks so the cell reads as one line", async () => {
  const wordy = {
    ...SKILLS[0],
    instructions: "Read the merged pull requests.\n\nGroup them by area,\nand lead with the verb.",
  };
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: [wordy] }) });
  renderSkills();
  await openInstalled();

  const row = (await screen.findByText("mine")).closest("tr") as HTMLElement;
  expect(within(row).getAllByRole("cell")[1]?.textContent).toBe(
    "Read the merged pull requests. Group them by area, and lead with the verb.",
  );
});

test("a workspace of built-in skills alone draws the blank", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: [SKILLS[1]] }) });
  renderSkills();
  await openInstalled();

  expect(await screen.findByText("No skill has been saved onto this workspace yet.")).toBeTruthy();
  expect(screen.queryByText("shipped")).toBeNull();
});

test("New skill stands beside the search and offers the two ways to make one", async () => {
  wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: [] }) });
  renderSkills();

  const trigger = await screen.findByRole("button", { name: /New skill/ });
  const toolbar = trigger.closest("div")?.parentElement as HTMLElement;
  expect(within(toolbar).getByPlaceholderText("Search")).toBeTruthy();

  await userEvent.click(trigger);
  expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
    "Create from chat",
    "Upload .md skill",
  ]);
});

test("Create from chat opens a new chat on the main agent and sends nothing", async () => {
  const { calls } = wire({ ...NO_COMMUNITY, "/skills": () => json({ skills: [] }) });
  renderSkills();

  await userEvent.click(await screen.findByRole("button", { name: /New skill/ }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Create from chat" }));

  expect(location.hash).toBe(newChatHash(AGENT.id));
  expect(calls.some((url) => url.includes("/chat"))).toBe(false);
});

test("an uploaded .md is saved under the name its frontmatter states, and says so", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();

  await screen.findByRole("button", { name: /New skill/ });
  const document = '---\nname: release-notes\ndescription: "Load when asked."\n---\n\nRead the tags.\n';
  pickFile("Release Notes.md", document);

  await waitFor(() => expect(posted.length).toBe(1));
  expect(posted[0]).toEqual({
    verb: "apply",
    kind: "skill",
    name: "release-notes",
    spec: { files: { "SKILL.md": document }, pinned: false },
    create_only: true,
  });
  expect(await screen.findByText("release-notes imported.")).toBeTruthy();
});

/** The directory's document would overwrite whatever the member has since made of the skill, so a
 *  name the workspace already holds does not open the review at all. */
test("a community skill the workspace already holds cannot be installed over", async () => {
  wire({
    "/skills": () => json({ skills: SKILLS }),
    "/skills/community": () =>
      json({ skills: [{ name: "mine", source: "acme/skills", installs: 12 }] }),
  });
  renderSkills();

  const row = (await screen.findByText("mine")).closest("tr") as HTMLElement;
  expect(row.textContent).toContain("Installed");
  expect(row.getAttribute("role")).toBeNull();
  expect(row.getAttribute("tabindex")).toBeNull();

  await userEvent.click(row);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("an .md naming no skill is refused here, because the save parses the name against the file", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();

  await screen.findByRole("button", { name: /New skill/ });
  pickFile("Weekly Digest.md", "Just a body, no frontmatter.\n");

  expect(
    await screen.findByText(
      "A skill file opens with a frontmatter block naming it: --- then name: my-skill.",
    ),
  ).toBeTruthy();
  expect(posted).toEqual([]);
});

test("an .md that would not fit one intent body is refused here, not 413'd at the far end", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();

  await screen.findByRole("button", { name: /New skill/ });
  pickFile("huge.md", "-".repeat(64 * 1024 + 1));

  expect(await screen.findByText("A skill file is at most 64 KB.")).toBeTruthy();
  expect(posted).toEqual([]);
});

/** 40 KB of quotes is under the size the picker is held to and over the size the body admits once
 *  each one is escaped, which is why the bound is taken on what crosses rather than on the file. */
test("an .md the size guard admits but JSON escaping grows past the body is still refused", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [] }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();

  await screen.findByRole("button", { name: /New skill/ });
  const quoted = '---\nname: quoted\n---\n\n' + '"'.repeat(40 * 1024);
  expect(quoted.length).toBeLessThan(64 * 1024);
  pickFile("quoted.md", quoted);

  expect(await screen.findByText("A skill file is at most 64 KB.")).toBeTruthy();
  expect(posted).toEqual([]);
});

/** An apply carries the whole file set and no generation, so landing one SKILL.md on a held name
 *  would drop the files bundled beside it and clear its pin, with nothing to undo it. */
test("an upload naming a skill the workspace already holds is refused before it can replace it", async () => {
  const posted: unknown[] = [];
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: SKILLS }),
    "/intents": (_url, init) => {
      posted.push(JSON.parse(String(init?.body)));
      return json({ applied: true, message: "Saved." });
    },
  });
  renderSkills();
  await openInstalled();

  await screen.findByText("mine");
  pickFile("mine.md", "---\nname: mine\n---\n\nSomething else entirely.\n");

  expect(
    await screen.findByText("mine is already saved. Open it to change what it says."),
  ).toBeTruthy();
  expect(posted).toEqual([]);
});

test("an upload the save refuses says so and stays on the page", async () => {
  wire({
    ...NO_COMMUNITY,
    "/skills": () => json({ skills: [] }),
    "/intents": () => json({ applied: false, message: "A deploy skill owns that name." }),
  });
  renderSkills();

  await screen.findByRole("button", { name: /New skill/ });
  pickFile("release-notes.md", "---\nname: release-notes\n---\n\nbody\n");

  expect(await screen.findByText("A deploy skill owns that name.")).toBeTruthy();
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

  const row = (await screen.findByText("mine")).closest("tr") as HTMLElement;
  await userEvent.click(within(row).getByRole("button", { name: "Actions for mine" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
  const asking = await screen.findByRole("dialog");
  await userEvent.click(within(asking).getByRole("button", { name: "Delete" }));

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

  expect(await screen.findByText("mine")).toBeTruthy();
  await userEvent.type(await screen.findByLabelText("Search skills"), "absent{enter}");

  expect(await screen.findByText("No skill matches this search.")).toBeTruthy();
  expect(screen.queryByText("mine")).toBeNull();
});

test("the writing form opens in the shared sheet, and the address carries no half-written skill", async () => {
  wire({ ...NO_COMMUNITY, ...MINE_OBJECT, "/skills": () => json({ skills: SKILLS }) });
  renderSkills();
  await openInstalled();

  await userEvent.click(await screen.findByText("mine"));

  const form = await screen.findByRole("dialog", { name: "mine" });
  expect((within(form).getByLabelText("Name") as HTMLInputElement).readOnly).toBe(true);
  expect(location.hash).toBe("#/workspace/skills");
});
