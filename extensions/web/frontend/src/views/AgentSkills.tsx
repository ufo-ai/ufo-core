import { Fragment, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, Input, Search, Textarea } from "@/components/ui/field";
import { Filter } from "@/components/ui/filter";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { rowControl } from "@/kernel/row";
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
import { getJson, postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { Agent } from "@/lib/types";

type Skill = { name: string; description: string; origin: string; instructions: string };

type CommunitySkill = { name: string; source: string; installs: number };

/** One skill reads the same whether it is already on the agent or still in the directory: the
 *  name, the one plain line under it, what it states about itself, and the acts it carries. The
 *  line is where the skill came from: `Custom` or `Built-in` for a skill the agent holds, the
 *  source repository and the install count for a directory row, which the directory publishes no
 *  description for. */
type SkillRow = {
  key: string;
  name: string;
  body: string;
  open: () => void;
  action: ReactNode;
};

type CommunityDocument = {
  name: string;
  description: string;
  instructions: string;
  document: string;
};

const SOURCE_URL = "https://github.com/";

/** A skill is prose the member reads, not a file they edit, so its boxes take the sans face — the
 *  mono `Textarea` states code where there is none. Only the family changes: the size stays the
 *  one every control in the portal is set at, and mono's narrow step goes with the mono. */
const PROSE = "font-sans text-subtitle narrow:text-ui";

const COMMUNITY = "community";
const INSTALLED = "installed";

const ORIGINS: { label: string; value: string }[] = [
  { label: "Custom", value: "member" },
  { label: "Built-in", value: "deploy" },
];

const NARROWINGS = [
  { label: "Community", value: COMMUNITY },
  { label: "Installed", value: INSTALLED },
];

function installsLabel(installs: number): string {
  if (installs >= 1_000_000) return (installs / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M installs";
  if (installs >= 1_000) return (installs / 1_000).toFixed(1).replace(/\.0$/, "") + "K installs";
  return installs + (installs === 1 ? " install" : " installs");
}

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

function SkillItems({ rows }: { rows: SkillRow[] }) {
  return (
    <ItemGroup>
      {rows.map((row, index) => {
        const control = rowControl(row.open);
        return (
          <Fragment key={row.key}>
            {index ? <ItemSeparator /> : null}
            <Item {...control} className={cn(control.className, "hover:bg-fill-hover")}>
              <ItemContent>
                <ItemTitle>{row.name}</ItemTitle>
                <ItemDescription>{row.body}</ItemDescription>
              </ItemContent>
              <ItemActions>{row.action}</ItemActions>
            </Item>
          </Fragment>
        );
      })}
    </ItemGroup>
  );
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
  const [narrowed, setNarrowed] = useState(COMMUNITY);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [viewing, setViewing] = useState<Skill | null>(null);
  const [submitted, setSubmitted] = useState("");
  const [adding, setAdding] = useState<CommunityDocument | null>(null);
  const [addNotice, setAddNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<{ skills: Skill[] }>("/agents/" + agent.id + "/skills", reloads);
  const known = state.phase === "ready" ? state.payload.skills : null;
  const browsing = narrowed === COMMUNITY;
  const communityState = usePanelRead<{ skills: CommunitySkill[] }>(
    browsing
      ? "/agents/" +
          agent.id +
          "/skills/community" +
          (submitted ? "?q=" + encodeURIComponent(submitted) : "")
      : null,
  );

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
        files: {
          "SKILL.md": skillDocument(named, description.trim(), instructions),
        },
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

  async function review(skill: CommunitySkill) {
    if (busy) return;
    setBusy(true);
    const fetched = await getJson<CommunityDocument>(
      "/agents/" + agent.id + "/skills/community/" + skill.source + "/" + skill.name,
    );
    setBusy(false);
    if (!fetched.ok) {
      setToast({
        title: skill.name + " did not open.",
        description: fetched.message,
      });
      return;
    }
    setAddNotice(QUIET);
    setAdding(fetched.payload);
  }

  function ownRows(skills: Skill[]): SkillRow[] {
    return ordered(skills).map((skill) => ({
      key: skill.name,
      name: skill.name,
      body: origin(skill),
      open: () => setViewing(skill),
      action: (
        <Button variant="outline" disabled>
          Installed
        </Button>
      ),
    }));
  }

  function communityRows(skills: CommunitySkill[]): SkillRow[] {
    return skills.map((skill) => {
      const installed = (known ?? []).some((one) => one.name === skill.name);
      return {
        key: skill.source + "/" + skill.name,
        name: skill.name,
        body: skill.source + " · " + installsLabel(skill.installs),
        open: () => void review(skill),
        action: (
          <>
            <a
              href={SOURCE_URL + skill.source}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                buttonVariants({ variant: "outline" }),
                "border-transparent no-underline transition-opacity",
                "opacity-(--opacity-muted) hover:opacity-100",
              )}
            >
              Source ↗
            </a>
            {installed ? (
              <Button variant="outline" disabled>
                Installed
              </Button>
            ) : (
              <Button variant="outline" disabled={busy} onClick={() => void review(skill)}>
                Install
              </Button>
            )}
          </>
        ),
      };
    });
  }

  async function install() {
    if (busy || !adding) return;
    setBusy(true);
    const outcome = await postIntent(agent.id, {
      verb: "apply",
      kind: "skill",
      name: adding.name,
      spec: { files: { "SKILL.md": adding.document } },
    });
    setBusy(false);
    if (!outcome.applied) {
      setAddNotice(outcomeNotice(outcome));
      return;
    }
    setAdding(null);
    setNotice(outcomeNotice(outcome));
    setReloads((count) => count + 1);
  }

  return (
    <>
      <OutcomeNotice state={notice} />
      <Section
        bar={
          <div className="flex w-full flex-col gap-md">
            <div className="flex items-stretch gap-sm">
              <Search
                label="Search skills"
                placeholder="Search skills"
                className="flex-1"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onSubmit={() => setSubmitted(query.trim())}
                onKeyDown={(event) => {
                  if (event.key === "Enter") setSubmitted(query.trim());
                }}
              />
              <Button variant="send" size="bar" onClick={() => setWriting(true)}>
                New skill
              </Button>
            </div>
            <Filter all={false} options={NARROWINGS} value={narrowed} onChange={setNarrowed} />
          </div>
        }
      >
        {browsing ? (
          <Panel state={communityState} shape="table">
            {(payload) => {
              if (!payload.skills.length)
                return (
                  <PanelBlank
                    body={
                      submitted
                        ? "No community skill matches this search."
                        : "The skill directory listed nothing."
                    }
                  />
                );
              return <SkillItems rows={communityRows(payload.skills)} />;
            }}
          </Panel>
        ) : (
          <Panel state={state} shape="table">
            {(payload) => {
              if (!payload.skills.length)
                return (
                  <PanelBlank body={"No skill has been saved onto " + agent.name + " yet."} />
                );
              const matched = payload.skills.filter((skill) =>
                (skill.name + " " + skill.description).toLowerCase().includes(query.toLowerCase()),
              );
              if (!matched.length) return <PanelBlank body="No skill matches this search." />;
              return <SkillItems rows={ownRows(matched)} />;
            }}
          </Panel>
        )}
      </Section>

      {viewing ? (
        <Dialog open onOpenChange={(next) => (next ? undefined : setViewing(null))}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{viewing.name}</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-xl">
              <Field label="Description" htmlFor="skill-view-description">
                <Textarea
                  id="skill-view-description"
                  readOnly
                  rows={3}
                  className={PROSE}
                  value={viewing.description}
                />
              </Field>
              <Field label="Instructions" htmlFor="skill-view-instructions">
                <Textarea
                  id="skill-view-instructions"
                  readOnly
                  rows={12}
                  className={PROSE}
                  value={viewing.instructions}
                />
              </Field>
            </div>
            <DialogFooter
              leave="Close"
              lead={
                viewing.origin === "member" ? (
                  <ConfirmButton
                    verb="Delete"
                    className="px-3xl py-md"
                    disabled={busy}
                    onClick={() => {
                      const named = viewing.name;
                      setViewing(null);
                      void submitIntent({ verb: "delete", kind: "skill", name: named });
                    }}
                  />
                ) : null
              }
            />
          </DialogContent>
        </Dialog>
      ) : null}

      {adding ? (
        <Dialog open onOpenChange={(next) => (next ? undefined : setAdding(null))}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{adding.name}</DialogTitle>
            </DialogHeader>
            <OutcomeNotice state={addNotice} />
            <div className="flex flex-col gap-xl">
              <Field label="Description" htmlFor="skill-add-description">
                <Textarea
                  id="skill-add-description"
                  readOnly
                  rows={3}
                  className={PROSE}
                  value={adding.description}
                />
              </Field>
              <Field label="Instructions" htmlFor="skill-add-instructions">
                <Textarea
                  id="skill-add-instructions"
                  readOnly
                  rows={12}
                  className={PROSE}
                  value={adding.instructions}
                />
              </Field>
            </div>
            <DialogFooter>
              <Button variant="send" busy={busy} onClick={() => void install()}>
                Install
              </Button>
            </DialogFooter>
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
                  className={PROSE}
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

      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
