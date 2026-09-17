import { IconChevronDown, IconDotsVertical } from "@tabler/icons-react";
import { useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/field";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Filter } from "@/components/ui/filter";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Sheet } from "@/components/ui/sheet";
import { ACTS, Td, TdActs, TdFill, TdWhole } from "@/components/ui/table";
import type { Placement } from "@/kernel/pager";
import { PageToolbar } from "@/kernel/pane";
import { DataTable } from "@/kernel/table";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { getJson, postIntent } from "@/lib/api";
import { useMainAgent } from "@/lib/mainAgent";
import { setPendingAsk } from "@/lib/pendingAsk";
import { newChatHash } from "@/lib/route";
import { navigate } from "@/lib/router";
import { useSurfaces } from "@/lib/surfaces";

type Skill = {
  name: string;
  description: string;
  origin: string;
  instructions: string;
  depends: string[];
  agents: string[];
};

type SkillDetail = {
  spec: {
    files: Record<string, { sha256: string }>;
    pinned: boolean;
  } | null;
  generation: string | null;
};

type CommunitySkill = { name: string; source: string; installs: number };

type CommunityDocument = {
  name: string;
  description: string;
  instructions: string;
  document: string;
};

const SOURCE_URL = "https://github.com/";

const PROSE = "font-sans text-subtitle narrow:text-ui";

const COMMUNITY = "community";
const INSTALLED = "installed";

const BUILTIN = "deploy";

const NARROWINGS = [
  { label: "Community", value: COMMUNITY },
  { label: "Installed", value: INSTALLED },
];

const NO_SKILLS = "No skill has been saved onto this workspace yet.";

const CREATE_ASK = "Create a new skill: ";
const UPLOAD_ACCEPT = ".md,text/markdown";
/** `INTENT_MAX_BYTES` in `panels.py`: the whole intent crosses as one body, so the document plus its
 *  envelope is what has to fit, and a file past it is refused here rather than 413'd at the far end. */
const INTENT_MAX_BYTES = 65_536;
const UPLOAD_MAX_KB = 64;
const TOO_LONG = "A skill file is at most " + UPLOAD_MAX_KB + " KB.";
const alreadySaved = (name: string) => name + " is already saved. Open it to change what it says.";
const NO_FRONTMATTER =
  "A skill file opens with a frontmatter block naming it: --- then name: my-skill.";
/** The name the document states for itself. The save parses the content against the name the intent
 *  carries, so a name taken from anywhere else — a filename, a lowercased spelling — is refused. */
const FRONTMATTER_NAME = /^---\r?\n([\s\S]*?)\r?\n---/;
const STATED_NAME = /^name:[ \t]*(?:"([^"]*)"|'([^']*)'|([^\r\n]*?))[ \t]*$/m;

function statedName(document: string): string | null {
  const block = FRONTMATTER_NAME.exec(document);
  if (!block) return null;
  const stated = STATED_NAME.exec(block[1]);
  if (!stated) return null;
  return (stated[1] ?? stated[2] ?? stated[3] ?? "").trim() || null;
}

function installsLabel(installs: number): string {
  if (installs >= 1_000_000) return (installs / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M installs";
  if (installs >= 1_000) return (installs / 1_000).toFixed(1).replace(/\.0$/, "") + "K installs";
  return installs + (installs === 1 ? " install" : " installs");
}

function ordered(skills: Skill[]): Skill[] {
  return [...skills].sort((left, right) => left.name.localeCompare(right.name));
}

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

const OWN_COLUMNS = [
  { label: "Skill", whole: true },
  { label: "Instructions", fill: true },
  { label: "", acts: true },
];

/** The workflow runs to many lines and a cell holds one, so the breaks close up rather than
 *  rendering as the single space a truncated line would end on. */
function opening(instructions: string): string {
  return instructions.replace(/\s+/g, " ").trim();
}

const COMMUNITY_COLUMNS = [
  { label: "Skill", whole: true },
  { label: "Source", fill: true },
  { label: "Installs", fact: true },
];

/** The name and what it does are what the member came to read; the directory's counts recede. */
const FACT = "text-ink";

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
  const [draft, setDraft] = useState<{
    skill: Skill;
    generation: string | null;
    stored: Record<string, { sha256: string }>;
    pinned: boolean;
  } | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const surfaces = useSurfaces();
  const offered = NARROWINGS.filter(({ value }) =>
    value === COMMUNITY ? surfaces["community-skills"] : surfaces["installed-skills"],
  );
  const [narrowed, setNarrowed] = useState(offered[0]?.value ?? COMMUNITY);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const [adding, setAdding] = useState<CommunityDocument | null>(null);
  const [dropping, setDropping] = useState<Skill | null>(null);
  const file = useRef<HTMLInputElement>(null);
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

  /** Hands the composer the opening line and opens a new chat on the main agent, the way every
   *  other screen starts one: the member writes what the skill should do, the agent saves it. */
  function startFromChat() {
    if (!mainAgent) return;
    setPendingAsk(mainAgent.id, CREATE_ASK, false);
    navigate(newChatHash(mainAgent.id));
  }

  async function upload(picked: File) {
    if (busy || !mainAgent) return;
    const overlong = { title: picked.name + " did not import.", description: TOO_LONG };
    if (picked.size > UPLOAD_MAX_KB * 1024) {
      setToast(overlong);
      return;
    }
    // jsdom ships no `Blob.text()`, so the bytes are decoded rather than read as text.
    const document = new TextDecoder().decode(await picked.arrayBuffer());
    const named = statedName(document);
    if (!named) {
      setToast({ title: picked.name + " did not import.", description: NO_FRONTMATTER });
      return;
    }
    if (holds(named)) {
      setToast({ title: picked.name + " did not import.", description: alreadySaved(named) });
      return;
    }
    // `create_only` is the save's own answer to a name already taken: an apply carries the whole
    // file set, and the listing this screen read cannot rule out a row it never saw.
    const intent = {
      verb: "apply",
      kind: "skill",
      name: named,
      spec: { files: { "SKILL.md": document }, pinned: false },
      create_only: true,
    };
    // The bound is on what crosses, not on what was picked: escaping a document into JSON grows it.
    if (new TextEncoder().encode(JSON.stringify(intent)).length > INTENT_MAX_BYTES) {
      setToast(overlong);
      return;
    }
    setBusy(true);
    const outcome = await postIntent(mainAgent.id, intent);
    setBusy(false);
    if (!outcome.applied) {
      setToast({ title: picked.name + " did not import.", description: outcome.message });
      return;
    }
    setToast({ title: named + " imported." });
    setNarrowed(INSTALLED);
    setReloads((count) => count + 1);
  }

  async function write(skill: Skill) {
    if (busy || !mainAgent) return;
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
    setSaveNotice(QUIET);
    setDraft({
      skill,
      generation: detail.payload.generation,
      stored: detail.payload.spec.files,
      pinned: detail.payload.spec.pinned,
    });
    setName(skill.name);
    setDescription(skill.description);
    setInstructions(skill.instructions);
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

  function OwnTable({ skills, note }: { skills: Skill[]; note?: string }) {
    return (
      <DataTable
        columns={OWN_COLUMNS}
        rows={ordered(skills)}
        rowKey={(skill) => skill.name}
        lede
        empty={NO_SKILLS}
        note={note}
        open={(skill) => () => void write(skill)}
      >
        {(skill) => (
          <>
            <TdWhole className={FACT}>{skill.name}</TdWhole>
            <TdFill>{opening(skill.instructions)}</TdFill>
            <TdActs>
              <div className={ACTS}>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="quiet"
                      size="icon"
                      disabled={busy}
                      aria-label={"Actions for " + skill.name}
                    >
                      <IconDotsVertical aria-hidden />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => setDropping(skill)}>Delete</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </TdActs>
          </>
        )}
      </DataTable>
    );
  }

  /** An apply carries the whole file set, so landing one SKILL.md on a held name drops every file
   *  bundled beside it and clears its pin. The editor is the way to change a saved skill. */
  function holds(name: string): boolean {
    return (known ?? []).some((one) => one.name === name);
  }

  function CommunityTable({ skills, note }: { skills: CommunitySkill[]; note?: string }) {
    return (
      <DataTable
        columns={COMMUNITY_COLUMNS}
        rows={skills}
        rowKey={(skill) => skill.source + "/" + skill.name}
        lede
        empty="The skill directory listed nothing."
        note={note}
        open={(skill) => (holds(skill.name) ? null : () => void review(skill))}
        act={(skill) => (holds(skill.name) ? "Installed" : "Install")}
      >
        {(skill) => (
          <>
            <TdWhole className={FACT}>{skill.name}</TdWhole>
            <TdFill>
              <a
                href={SOURCE_URL + skill.source}
                target="_blank"
                rel="noopener noreferrer"
                className="text-ink-soft no-underline hover:text-ink"
              >
                {skill.source}
              </a>
            </TdFill>
            <Td>{installsLabel(skill.installs)}</Td>
          </>
        )}
      </DataTable>
    );
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

  const sheet =
    draft !== null ? (
      <Sheet open title={draft.skill.name} onClose={close}>
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
              readOnly
              aria-describedby="skill-name-description"
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
          <div className="flex items-center justify-end gap-xl">
            <Button type="submit" variant="send" size="bar" busy={busy}>
              Save
            </Button>
          </div>
        </form>
      </Sheet>
    ) : null;

  return (
    <>
      <OutcomeNotice state={notice} />
      <PageToolbar>
        {mainAgent ? (
          <div className="ml-auto flex shrink-0 items-center">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="send" size="bar">
                  New skill
                  <IconChevronDown aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => startFromChat()}>
                  Create from chat
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => file.current?.click()}>
                  Upload .md skill
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        ) : null}
      </PageToolbar>
      <input
        ref={file}
        data-testid="skill-upload"
        type="file"
        accept={UPLOAD_ACCEPT}
        hidden
        onChange={(event) => {
          const picked = event.target.files?.[0];
          event.target.value = "";
          if (picked) void upload(picked);
        }}
      />
      <Section
        bar={
          offered.length > 1 ? (
            <Filter
              all={false}
              options={offered}
              value={narrowed}
              onChange={(next) => {
                setNarrowed(next);
                onPlace({ q: undefined });
              }}
            />
          ) : null
        }
      >
        {browsing ? (
          <Panel state={communityState} shape="table">
            {(payload) => (
              <CommunityTable
                skills={payload.skills}
                note={query ? "No community skill matches this search." : undefined}
              />
            )}
          </Panel>
        ) : (
          <Panel state={state} shape="table">
            {(payload) => {
              const own = payload.skills.filter((skill) => skill.origin !== BUILTIN);
              const matched = own.filter((skill) =>
                (skill.name + " " + skill.description).toLowerCase().includes(query.toLowerCase()),
              );
              return (
                <OwnTable
                  skills={matched}
                  note={own.length ? "No skill matches this search." : undefined}
                />
              );
            }}
          </Panel>
        )}
      </Section>

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

      <Dialog open={dropping !== null} onOpenChange={(next) => (next ? undefined : setDropping(null))}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete {dropping?.name}?</DialogTitle>
            <DialogDescription>
              Every agent that loads this skill stops loading it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              busy={busy}
              onClick={() => {
                const named = dropping?.name;
                setDropping(null);
                if (named) void submitIntent({ verb: "delete", kind: "skill", name: named });
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Toast state={toast} onDone={() => setToast(SILENT)} />
    </>
  );
}
