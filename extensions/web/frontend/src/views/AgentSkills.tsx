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
import { CardGrid, codeSpans } from "@/kernel/cards";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelBlank,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import type { Agent } from "@/lib/types";

type Skill = { name: string; description: string; origin: string; instructions: string };

const ORIGINS: { label: string; value: string }[] = [
  { label: "Custom", value: "member" },
  { label: "Built-in", value: "deploy" },
];

const ORIGIN_ORDER = ORIGINS.map((entry) => entry.value);

function origin(skill: Skill): string {
  return ORIGINS.find((entry) => entry.value === skill.origin)?.label ?? skill.origin;
}

function ordered(skills: Skill[]): Skill[] {
  return [...skills].sort((left, right) => {
    const leftRank = ORIGIN_ORDER.indexOf(left.origin);
    const rightRank = ORIGIN_ORDER.indexOf(right.origin);
    const normalizedLeft = leftRank === -1 ? ORIGIN_ORDER.length : leftRank;
    const normalizedRight = rightRank === -1 ? ORIGIN_ORDER.length : rightRank;
    if (normalizedLeft !== normalizedRight) return normalizedLeft - normalizedRight;
    return left.name.localeCompare(right.name);
  });
}

const NAME_PATTERN = "[a-z0-9](?:[a-z0-9-]*[a-z0-9])?";
const NAME_MAX = 64;

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

export function AgentSkills({ agent }: { agent: Agent }) {
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
  const [viewing, setViewing] = useState<Skill | null>(null);
  const state = usePanelRead<{ skills: Skill[] }>("/agents/" + agent.id + "/skills", reloads);
  const known = state.phase === "ready" ? state.payload.skills : null;

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
    <>
      <OutcomeNotice state={notice} />
      <Section
        title="Skills"
        bar={
          known?.length ? (
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
            </>
          ) : null
        }
      >
        <Panel state={state} shape="cards">
          {(payload) => {
            if (!payload.skills.length)
              return (
                <PanelBlank
                  body={"No skill has been saved onto " + agent.name + " yet."}
                  action={
                    <Button variant="outline" onClick={() => setWriting(true)}>
                      New skill
                    </Button>
                  }
                />
              );
            const matched = ordered(payload.skills).filter(
              (skill) =>
                (!narrowed || skill.origin === narrowed) &&
                (skill.name + " " + skill.description)
                  .toLowerCase()
                  .includes(query.toLowerCase()),
            );
            if (!matched.length) return <PanelBlank body="No skill matches this search." />;
            return (
              <CardGrid
                rows={matched}
                rowKey={(skill) => skill.name}
                open={(skill) => () => setViewing(skill)}
                primary={(skill) => skill.name}
                status={(skill) => origin(skill)}
                body={(skill) => codeSpans(skill.description)}
                action={(skill) =>
                  skill.origin === "member" ? (
                    <ConfirmButton
                      verb="Delete"
                      variant="row"
                      disabled={busy}
                      onClick={() =>
                        submitIntent({ verb: "delete", kind: "skill", name: skill.name })
                      }
                    />
                  ) : null
                }
              />
            );
          }}
        </Panel>
      </Section>

      {viewing ? (
        <Dialog open onOpenChange={(next) => (next ? undefined : setViewing(null))}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{viewing.name}</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-xl">
              <Field label="Description" htmlFor="skill-view-description">
                <Input id="skill-view-description" readOnly value={viewing.description} />
              </Field>
              <Field label="Instructions" htmlFor="skill-view-instructions">
                <Textarea
                  id="skill-view-instructions"
                  readOnly
                  rows={12}
                  value={viewing.instructions}
                />
              </Field>
            </div>
            <DialogFooter leave="Close" />
          </DialogContent>
        </Dialog>
      ) : null}

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
  );
}
