import { useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import { Td } from "@/components/ui/table";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import type { Agent } from "@/lib/types";

type Skill = { name: string; description: string; origin: string };

/** The wire files a skill under `member` or `deploy`. A member reads where it came from: one they
 *  saved onto this agent, or one the deploy ships to every agent in the workspace. */
const ORIGINS: { label: string; value: string }[] = [
  { label: "This agent", value: "member" },
  { label: "Deploy", value: "deploy" },
];

function origin(skill: Skill): string {
  return ORIGINS.find((entry) => entry.value === skill.origin)?.label ?? skill.origin;
}

/** The grammar every object name obeys, stated to the browser so a name the lane would refuse is
 *  refused in the field the member is typing it into. */
const NAME_PATTERN = "[a-z0-9](?:[a-z0-9-]*[a-z0-9])?";
const NAME_MAX = 64;

/** `SKILL.md` is one file with a fixed header, and the header's `name` must equal the name the
 *  skill is filed under — a form that asks for both invites a mismatch the lane can only refuse.
 *  So the member states name, description, and instructions once each, and the file is composed
 *  here. The description is emitted as a quoted scalar, which is what makes a colon in it safe. */
function skillDocument(name: string, description: string, instructions: string): string {
  return [
    "---",
    "name: " + name,
    "description: " + JSON.stringify(description),
    "---",
    "",
    instructions.trim(),
    "",
  ].join("\n");
}

export function Skills({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [saveNotice, setSaveNotice] = useState<NoticeState>(QUIET);
  const [writing, setWriting] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [narrowed, setNarrowed] = useState("");
  const state = usePanelRead<{ skills: Skill[] }>("/agents/" + agent.id + "/skills", reloads);

  async function submitIntent(intent: unknown) {
    setBusy(true);
    const outcome = await postIntent(agent.id, intent);
    setBusy(false);
    setNotice(outcomeNotice(outcome));
    if (outcome.applied) setReloads((count) => count + 1);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const named = name.trim();
    if (busy) return;
    setBusy(true);
    const outcome = await postIntent(agent.id, {
      verb: "apply",
      kind: "skill",
      name: named,
      spec: {
        files: { "SKILL.md": skillDocument(named, description.trim(), instructions) },
      },
    });
    setBusy(false);
    setSaveNotice(outcomeNotice(outcome));
    if (!outcome.applied) return;
    setReloads((count) => count + 1);
    close();
  }

  function close() {
    setWriting(false);
    setSaveNotice(QUIET);
    setName("");
    setDescription("");
    setInstructions("");
  }

  return (
    <Panel state={state}>
      {(payload) => (
        <>
          <OutcomeNotice state={notice} />
          <Section
            title="Skills"
            bar={
              <>
                <Input
                  type="search"
                  aria-label="Search"
                  placeholder="Search"
                  className="max-w-control-row"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
                <Filter options={ORIGINS} value={narrowed} onChange={setNarrowed} />
                <Button variant="send" onClick={() => setWriting(true)}>
                  New skill
                </Button>
                <Button onClick={() => setReloads((count) => count + 1)}>Refresh</Button>
              </>
            }
          >
            <DataTable
              columns={["Name", "Description", "Source", ""]}
              rows={payload.skills.filter(
                (skill) =>
                  (!narrowed || skill.origin === narrowed) &&
                  (skill.name + " " + skill.description)
                    .toLowerCase()
                    .includes(query.toLowerCase()),
              )}
              rowKey={(skill) => skill.name}
              empty={"No skill has been saved onto " + agent.name + " yet."}
              note={query || narrowed ? "No skill matches this search." : undefined}
            >
              {(skill) => (
                <>
                  <Td>{skill.name}</Td>
                  <Td>{skill.description}</Td>
                  <Td>{origin(skill)}</Td>
                  <Td>
                    {skill.origin === "member" ? (
                      <ConfirmButton
                        verb="Delete"
                        variant="row"
                        disabled={busy}
                        onClick={() =>
                          submitIntent({ verb: "delete", kind: "skill", name: skill.name })
                        }
                      />
                    ) : null}
                  </Td>
                </>
              )}
            </DataTable>
          </Section>

          {writing ? (
            <Dialog open onOpenChange={(next) => (next ? undefined : close())}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>New skill</DialogTitle>
                </DialogHeader>
                <OutcomeNotice state={saveNotice} />
                <form id="save-skill" onSubmit={save} className="flex flex-col gap-xl">
                  <Field
                    label="Name"
                    htmlFor="skill-name"
                    description="Lowercase letters, digits, and hyphens."
                  >
                    <Input
                      id="skill-name"
                      required
                      pattern={NAME_PATTERN}
                      maxLength={NAME_MAX}
                      aria-describedby="skill-name-description"
                      placeholder="release-notes"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                    />
                  </Field>
                  <Field
                    label="Description"
                    htmlFor="skill-description"
                    description="The agent reads this to decide when to load the skill."
                  >
                    <Input
                      id="skill-description"
                      required
                      aria-describedby="skill-description-description"
                      placeholder="Load when a member asks to draft release notes."
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                    />
                  </Field>
                  <Field label="Instructions" htmlFor="skill-instructions">
                    <Textarea
                      id="skill-instructions"
                      required
                      placeholder={
                        "Read the merged pull requests since the last tag.\nGroup them by area, and lead each line with the verb."
                      }
                      value={instructions}
                      onChange={(event) => setInstructions(event.target.value)}
                    />
                  </Field>
                </form>
                <DialogFooter>
                  <Button type="submit" form="save-skill" variant="send" busy={busy}>
                    Save
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          ) : null}
        </>
      )}
    </Panel>
  );
}
