import { Fragment, useState, type FormEvent, type ReactNode } from "react";

import { Button, ConfirmButton, buttonVariants } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
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
import { Sheet } from "@/components/ui/sheet";
import type { Placement } from "@/kernel/pager";
import { PageToolbar, usePageAct, usePageSearch } from "@/kernel/pane";
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
import { useMainAgent } from "@/lib/mainAgent";

/** One saved or shipped skill as the workspace reads it. `depends` and `agents` are the
 *  frontmatter's routing metadata — a save regenerates SKILL.md, so it writes them back. */
type Skill = {
  name: string;
  description: string;
  origin: string;
  instructions: string;
  depends: string[];
  agents: string[];
};

/** What an edit reads from the skill's object detail before the panel opens: the generation the
 *  save must carry back, every stored file by digest so the ones beside SKILL.md survive the save,
 *  and the always-show mark, which the form never shows and the save states back rather than
 *  clearing it to the spec's unpinned default. */
type SkillDetail = {
  spec: {
    files: Record<string, { sha256: string }>;
    pinned: boolean;
  } | null;
  generation: string | null;
};

type CommunitySkill = { name: string; source: string; installs: number };

/** One skill reads the same whether the workspace already holds it or it is still in the directory:
 *  the name, the one plain line under it, what it states about itself, and the acts it carries. The
 *  line is where the skill came from: `Custom` or `Built-in` for a skill the workspace holds, the
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

const NO_SKILLS = "No skill has been saved onto this workspace yet.";

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

function skillDocument(
  name: string,
  description: string,
  instructions: string,
  depends: string[],
  agents: string[],
): string {
  const metadata: string[] = [];
  if (depends.length) metadata.push("  depends: " + JSON.stringify(depends));
  if (agents.length) metadata.push("  agents: " + JSON.stringify(agents));
  return [
    "---",
    "name: " + name,
    "description: " + JSON.stringify(description),
    ...(metadata.length ? ["metadata:", ...metadata] : []),
    "---",
    "",
    instructions.trim(),
    "",
  ].join("\n");
}

/** The whole file set one save carries: the workflow as it now reads, and every stored file beside
 *  SKILL.md kept by its digest — an apply states the skill's files completely, so a file left out
 *  of it is a file deleted. */
function skillFiles(
  name: string,
  description: string,
  instructions: string,
  skill: Skill | null,
  stored: Record<string, { sha256: string }>,
): Record<string, unknown> {
  return {
    "SKILL.md": skillDocument(
      name,
      description,
      instructions,
      skill?.depends ?? [],
      skill?.agents ?? [],
    ),
    ...Object.fromEntries(
      Object.entries(stored)
        .filter(([path]) => path !== "SKILL.md")
        .map(([path, ref]) => [path, { sha256: ref.sha256 }]),
    ),
  };
}

function SkillItems({ rows }: { rows: SkillRow[] }) {
  return (
    <ItemGroup>
      {rows.map((row, index) => {
        const control = rowControl(row.open);
        return (
          <Fragment key={row.key}>
            {index ? <ItemSeparator /> : null}
            <Item {...control} className={cn(control.className, "hover:bg-fill")}>
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

/** The workspace's skills: the directory to install from, the set the workspace holds, and the acts
 *  that write it. The set belongs to the workspace, and an app states in its own settings whether
 *  its turns load it — so the reads and the intents here ride the main agent, the one agent every
 *  member of the workspace reaches. */
export function WorkspaceSkills({
  place,
  onPlace,
}: {
  place: Placement;
  onPlace: (place: Placement) => void;
}) {
  const mainAgent = useMainAgent();
  const query = place.q ?? "";
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [saveNotice, setSaveNotice] = useState<NoticeState>(QUIET);
  /** The panel's subject: the skill being edited with the generation and stored files its detail
   *  read returned, null skill for one being written, and no draft at all while the panel is
   *  closed. The save carries `generation` back, so a save over someone else's newer save refuses
   *  instead of overwriting it. */
  const [draft, setDraft] = useState<{
    skill: Skill | null;
    generation: string | null;
    stored: Record<string, { sha256: string }>;
    pinned: boolean;
  } | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [narrowed, setNarrowed] = useState(COMMUNITY);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [viewing, setViewing] = useState<Skill | null>(null);
  const [adding, setAdding] = useState<CommunityDocument | null>(null);
  const [addNotice, setAddNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<{ skills: Skill[] }>(
    mainAgent ? "/agents/" + mainAgent.id + "/skills" : null,
    reloads,
  );
  const known = state.phase === "ready" ? state.payload.skills : null;
  const browsing = narrowed === COMMUNITY;
  const communityState = usePanelRead<{ skills: CommunitySkill[] }>(
    browsing && mainAgent
      ? "/agents/" +
          mainAgent.id +
          "/skills/community" +
          (query ? "?q=" + encodeURIComponent(query) : "")
      : null,
  );

  async function submitIntent(intent: unknown) {
    if (!mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, intent);
    setBusy(false);
    setNotice(outcomeNotice(outcome));
    if (outcome.applied) setReloads((count) => count + 1);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const named = name.trim();
    if (busy || !mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
      verb: "apply",
      kind: "skill",
      name: named,
      spec: {
        files: skillFiles(
          named,
          description.trim(),
          instructions,
          draft?.skill ?? null,
          draft?.stored ?? {},
        ),
        pinned: draft?.pinned ?? false,
      },
      ...(draft?.generation ? { generation: draft.generation } : {}),
    });
    setBusy(false);
    setSaveNotice(outcomeNotice(outcome));
    if (!outcome.applied) return;
    setReloads((count) => count + 1);
    close();
  }

  async function write(skill: Skill | null) {
    if (busy || !mainAgent) return;
    let generation: string | null = null;
    let stored: Record<string, { sha256: string }> = {};
    let pinned = false;
    if (skill) {
      setBusy(true);
      const detail = await getJson<SkillDetail>(
        "/objects/skill/" + skill.name + "?agent=" + mainAgent.id,
      );
      setBusy(false);
      if (!detail.ok || !detail.payload.spec || !detail.payload.generation) {
        setToast({
          title: skill.name + " did not open.",
          description: detail.ok ? "The skill has no readable record." : detail.message,
        });
        return;
      }
      generation = detail.payload.generation;
      stored = detail.payload.spec.files;
      pinned = detail.payload.spec.pinned;
    }
    setSaveNotice(QUIET);
    setDraft({ skill, generation, stored, pinned });
    setName(skill?.name ?? "");
    setDescription(skill?.description ?? "");
    setInstructions(skill?.instructions ?? "");
  }

  function close() {
    setDraft(null);
    setSaveNotice(QUIET);
    setName("");
    setDescription("");
    setInstructions("");
  }

  async function review(skill: CommunitySkill) {
    if (busy || !mainAgent) return;
    setBusy(true);
    const fetched = await getJson<CommunityDocument>(
      "/agents/" + mainAgent.id + "/skills/community/" + skill.source + "/" + skill.name,
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
      open: () => (skill.origin === "member" ? void write(skill) : setViewing(skill)),
      action:
        skill.origin === "member" ? (
          <Button variant="outline" disabled={busy} onClick={() => void write(skill)}>
            Edit
          </Button>
        ) : (
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
                "text-ink-soft hover:text-ink",
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
    if (busy || !adding || !mainAgent) return;
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, {
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

  const editing = draft?.skill ?? null;
  const sheet =
    draft !== null ? (
      <Sheet open title={editing ? editing.name : "New skill"} onClose={close}>
        <OutcomeNotice state={saveNotice} />
        <form onSubmit={save} className="flex flex-col gap-xl">
          <Field
            label="Name"
            htmlFor="skill-name"
            description="Lowercase letters, digits, and hyphens."
          >
            <Input
              id="skill-name"
              required
              readOnly={editing !== null}
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
            description="The app reads this to decide when to load the skill."
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
          <div className="flex items-center justify-between gap-xl">
            {editing ? (
              <ConfirmButton
                verb="Delete"
                className="px-3xl py-md"
                disabled={busy}
                onClick={() => {
                  const named = editing.name;
                  close();
                  void submitIntent({ verb: "delete", kind: "skill", name: named });
                }}
              />
            ) : (
              <span />
            )}
            <Button type="submit" variant="send" size="bar" busy={busy}>
              Save
            </Button>
          </div>
        </form>
      </Sheet>
    ) : null;

  const act = usePageAct(
    mainAgent ? (
      <Button variant="send" size="bar" onClick={() => void write(null)}>
        New skill
      </Button>
    ) : null,
  );

  const box = usePageSearch();

  return (
    <>
      {act}
      <OutcomeNotice state={notice} />
      {box ? <PageToolbar /> : null}
      <Section
        bar={
          <Filter
            all={false}
            options={NARROWINGS}
            value={narrowed}
            onChange={(next) => {
              setNarrowed(next);
              onPlace({ q: undefined });
            }}
          />
        }
      >
        {browsing ? (
          <Panel state={communityState} shape="table">
            {(payload) => {
              if (!payload.skills.length)
                return (
                  <PanelBlank
                    body={
                      query
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
              if (!payload.skills.length) return <PanelBlank body={NO_SKILLS} />;
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
            <DialogFooter leave="Close" />
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

      {sheet}

      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
