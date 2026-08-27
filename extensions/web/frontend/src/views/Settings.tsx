import { useEffect, useRef, useState } from "react";

import { IconPencil } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts, Group } from "@/components/ui/facts";
import { Hint, Textarea } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { type NoticeState, OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { AGENT_ICONS, AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { newChatHash, workspaceHash } from "@/lib/route";
import { navigate } from "@/lib/router";
import { setPendingAsk } from "@/lib/pendingAsk";
import { surfaceWord, webAudienceLabel } from "@/lib/audience";
import { Moment } from "@/lib/moments";
import type { Agent, SchemaProperty } from "@/lib/types";


/** What an extension-shipped agent still needs granted before it can work, or null once it is
 *  wired. Derived from the grants themselves, so the offer disappears on its own. */
type AgentSetupNeeded = { connectors: string[]; instructions: string };

/** What the button types on the member's behalf. It names the skill rather than the acts, so the
 *  agent reads its own outstanding grants at that moment instead of trusting a stale button. */
const SETUP_ASK = "Load the agent-setup skill and follow its instructions.";

/** The one mark whose label is not derived from its slug. Every other label is the slug's words
 *  with a capital on the first; the product is named ufo, and its name is written as it is
 *  written wherever it is read. The picker offers this mark to no app — it reaches the grid only as
 *  the main agent's own mark, led and checked like any mark from outside the offered set. */
const UFO_ICON = "ufo";

/** The act that keeps what a section was opened to change. The word on it is Save, and what it
 *  saves is the section it stands on — said in full to a reader who cannot see which section that
 *  is, and to any two of these standing at once. */
function SaveAction({
  what,
  busy,
  onSave,
}: {
  what: string;
  busy: boolean;
  onSave: () => void;
}) {
  return (
    <Button
      type="button"
      variant="send"
      size="bar"
      busy={busy}
      aria-label={"Save " + what}
      onClick={onSave}
    >
      Save
    </Button>
  );
}

/** The way into changing what a section states, and the act that commits it — one corner, two
 *  states. Reading is the default: the pencil is all a member sees until they choose to change
 *  something, and while they are changing it the same corner is the way to keep it. A section
 *  whose subject is itself the way in — the avatar, which a member presses directly — carries the
 *  save alone and no second pencil over it. */
function EditAction({
  editing,
  what,
  busy,
  onEdit,
  onSave,
}: {
  editing: boolean;
  what: string;
  busy: boolean;
  onEdit: () => void;
  onSave: () => void;
}) {
  if (editing) return <SaveAction what={what} busy={busy} onSave={onSave} />;
  return (
    <Button variant="quiet" size="icon" aria-label={"Edit " + what} onClick={onEdit}>
      <IconPencil aria-hidden />
    </Button>
  );
}

type SettingsPayload = {
  agent: {
    name: string;
    main: boolean;
    surfaces: string[];
    archivable: boolean;
    updated_at: string;
    prompt: string;
    prompt_digest: string;
    setup: AgentSetupNeeded | null;
  };
  spec: { icon: string; [field: string]: unknown };
  spec_schema: { properties?: Record<string, SchemaProperty> };
  models: string[];
  deploy: { sandbox_internet: boolean };
  audience?: string[] | null;
};

export function Settings({ agent, onArchived }: { agent: Agent; onArchived: () => void }) {
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<SettingsPayload>("/agents/" + agent.id + "/settings", reloads);
  const [values, setValues] = useState<Record<string, SpecValue>>({});
  const [prompt, setPrompt] = useState("");
  const [icon, setIcon] = useState("");
  // What the member has edited and what they have sent, held outside state on purpose: the seeding
  // effect below has to read them as they are when it runs, not as they were when the render that
  // scheduled it closed over them. An effect already scheduled when the member's first keystroke
  // commits would otherwise re-seed the field from the payload and swallow that keystroke.
  const settingsDirty = useRef(false);
  const settingsSaved = useRef<Record<string, SpecValue> | null>(null);
  const promptDirty = useRef(false);
  const promptSaved = useRef<string | null>(null);
  const iconDirty = useRef(false);
  const iconSaved = useRef<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  // Which sections the member has opened for editing. A section is read until they say otherwise,
  // and saving closes it again, so the panel returns to stating what is true.
  // A preference is kept the moment it is changed, so a member can change a second one while the
  // first is still in flight. Both carry the whole spec, so two answers racing would let the older
  // one land last and undo the newer choice. The intent is held here and sent one at a time: a save
  // already running picks the newest up when it comes back, so exactly one more request follows and
  // what becomes durable is always the member's last answer.
  const intended = useRef<Record<string, SpecValue> | null>(null);
  const saving = useRef(false);
  const [editingIcon, setEditingIcon] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  // What a save says once it has landed. It names the app rather than the act, because the act is
  // the button the member just pressed and the app is the thing that changed. A refusal is not
  // reported here: it stays on the panel, where it can be read back and answered.
  const [toast, setToast] = useState<ToastState>(SILENT);

  const payload = state.phase === "ready" ? state.payload : null;

  useEffect(() => {
    if (!payload) return;
    const properties = payload.spec_schema.properties ?? {};
    const projected = Object.fromEntries(
      Object.keys(properties).map((key) => [
        key,
        initialSpecValue(properties[key], payload.spec[key]),
      ]),
    );
    const sent = settingsSaved.current;
    const settingsLanded =
      sent !== null && Object.keys(properties).every((key) => projected[key] === sent[key]);
    if (!settingsDirty.current || settingsLanded) {
      setValues(projected);
      if (settingsLanded) {
        settingsDirty.current = false;
        settingsSaved.current = null;
        intended.current = null;
      }
    }
    const promptLanded =
      promptSaved.current !== null && payload.agent.prompt === promptSaved.current;
    if (!promptDirty.current || promptLanded) {
      setPrompt(payload.agent.prompt);
      if (promptLanded) {
        promptDirty.current = false;
        promptSaved.current = null;
      }
    }
    const iconLanded = iconSaved.current !== null && payload.spec.icon === iconSaved.current;
    if (!iconDirty.current || iconLanded) {
      setIcon(payload.spec.icon);
      if (iconLanded) {
        iconDirty.current = false;
        iconSaved.current = null;
      }
    }
  }, [payload]);

  return (
    <Panel state={state} shape="form">
      {(ready) => {
        const properties = ready.spec_schema.properties ?? {};
        const writable = ready.audience !== null && ready.audience !== undefined;

        async function save(spec: Record<string, SpecValue>) {
          intended.current = spec;
          if (saving.current) return;
          saving.current = true;
          setBusy(true);
          try {
            let sent: Record<string, SpecValue> | null = null;
            while (intended.current !== null && intended.current !== sent) {
              sent = intended.current;
              const outcome = await postIntent(agent.id, {
                verb: "apply",
                kind: "agent",
                name: ready.agent.name,
                spec: sent,
              });
              if (!outcome.applied) {
                // A refusal ends the run: every spec still queued carries the value that was just
                // refused, so sending it would only be refused again. What the member changed
                // afterwards is still drawn on the controls, though, and nothing has kept it — so
                // the read is taken again and the whole column goes back to stating what is stored.
                intended.current = null;
                settingsDirty.current = false;
                settingsSaved.current = null;
                setNotice(outcomeNotice(outcome));
                setReloads((count) => count + 1);
                return;
              }
              setNotice(QUIET);
              setToast({ title: agentName(ready.agent.name) + " saved." });
              settingsSaved.current = { ...sent };
            }
            setReloads((count) => count + 1);
          } finally {
            saving.current = false;
            setBusy(false);
          }
        }

        async function savePrompt() {
          if (busy) return;
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "apply",
            kind: "agent",
            name: ready.agent.name,
            spec: { prompt },
          });
          setBusy(false);
          if (!outcome.applied) {
            setNotice(outcomeNotice(outcome));
            return;
          }
          setNotice(QUIET);
          setToast({ title: agentName(ready.agent.name) + " saved." });
          promptSaved.current = prompt;
          setEditingPrompt(false);
          setReloads((count) => count + 1);
        }

        async function archive() {
          if (busy) return;
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "delete",
            kind: "agent",
            name: ready.agent.name,
          });
          setBusy(false);
          if (outcome.applied) {
            onArchived();
            return;
          }
          setNotice(outcomeNotice(outcome));
        }

        async function saveIcon() {
          if (busy) return;
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "apply",
            kind: "agent",
            name: ready.agent.name,
            spec: { icon },
          });
          setBusy(false);
          if (!outcome.applied) {
            setNotice(outcomeNotice(outcome));
            return;
          }
          setNotice(QUIET);
          setToast({ title: agentName(ready.agent.name) + " saved." });
          iconSaved.current = icon;
          setEditingIcon(false);
          setReloads((count) => count + 1);
        }

        // The setup ask goes to the composer, not to a send. Every grant binds to the agent whose
        // conversation it is made in, and the member who presses this is the speaker the granting
        // verbs gate on — so the member must be *in* that conversation, not merely the cause of
        // one. Founding it here would leave an id nothing else holds: no rail row, a read-only
        // conversation tab, and a connect link that lives only on the live tail.
        function startSetup() {
          setPendingAsk(agent.id, SETUP_ASK, false);
          navigate(newChatHash(agent.id));
        }

        return (
          <>
            <Toast state={toast} onDone={() => setToast(SILENT)} position="surface" />
            <OutcomeNotice state={notice} />
            {ready.agent.setup ? (
              <Group title="Set up">
                <Facts
                  rows={[
                    {
                      label: "Still needed",
                      value: ready.agent.setup.connectors
                        .map((provider) => provider + " account")
                        .join(", "),
                    },
                  ]}
                />
                {ready.agent.setup.instructions ? (
                  <Hint>{ready.agent.setup.instructions}</Hint>
                ) : null}
                <Button type="button" onClick={startSetup}>
                  Start setup
                </Button>
              </Group>
            ) : null}
            <Group
              title="Profile"
              action={
                editingIcon ? (
                  <SaveAction what="avatar" busy={busy} onSave={() => void saveIcon()} />
                ) : undefined
              }
            >
              <Facts
                rows={[
                  {
                    label: "Avatar",
                    value: (
                      <button
                        type="button"
                        aria-label="Edit avatar"
                        onClick={() => setEditingIcon(true)}
                        className={cn(
                          "group relative flex size-(--size-control) cursor-pointer items-center",
                          "justify-center rounded-avatar border-0 bg-fill-strong text-ink",
                        )}
                      >
                        <AgentIcon name={icon} />
                        <span
                          className={cn(
                            "absolute inset-0 hidden items-center justify-center",
                            "rounded-avatar bg-fill-strong group-hover:flex",
                          )}
                        >
                          <IconPencil className="size-(--size-glyph)" aria-hidden />
                        </span>
                      </button>
                    ),
                  },
                  { label: "App name", value: agentName(ready.agent.name) },
                ]}
              />
              {editingIcon ? (
                // The shortlist is drawn whole, so the member picks by eye rather than opening a
                // list of names, and the app's own mark leads it when the agent carries one from
                // outside — a mark the picker did not offer is still the mark it has, and a set
                // with nothing selected would state otherwise. A mark carries no name the member
                // reads; what a screen reader is given is the mark's own word.
                <fieldset
                  className={cn(
                    "m-0 mt-lg grid min-w-0 gap-xs border-0 p-0",
                    "grid-cols-[repeat(auto-fill,minmax(var(--size-touch),1fr))]",
                  )}
                >
                  <legend className="sr-only">Avatar</legend>
                  {[
                    ...(icon && !Object.hasOwn(AGENT_ICONS, icon) ? [icon] : []),
                    ...Object.keys(AGENT_ICONS),
                  ].map((slug) => {
                    const word = slug.replaceAll("-", " ");
                    const label =
                      slug === UFO_ICON ? UFO_ICON : word[0].toUpperCase() + word.slice(1);
                    return (
                      <label
                        key={slug}
                        className={cn(
                          "relative flex h-(--size-touch) cursor-pointer items-center justify-center",
                          "rounded-control border border-edge text-ink-soft",
                          "transition-colors duration-100 ease-control hover:bg-fill",
                          "has-[>input:focus-visible]:border-edge-strong",
                          "has-[>input:checked]:border-primary has-[>input:checked]:bg-fill",
                          "has-[>input:checked]:text-ink",
                        )}
                      >
                        <input
                          type="radio"
                          name="agent-icon"
                          value={slug}
                          checked={icon === slug}
                          aria-label={label}
                          onChange={() => {
                            iconDirty.current = true;
                            iconSaved.current = null;
                            setIcon(slug);
                          }}
                          className="absolute inset-0 size-full cursor-pointer opacity-0"
                        />
                        <AgentIcon name={slug} />
                      </label>
                    );
                  })}
                </fieldset>
              ) : null}
            </Group>


            <Group
              title="Prompt"
              action={
                writable ? (
                  <EditAction
                    editing={editingPrompt}
                    what="prompt"
                    busy={busy}
                    onEdit={() => setEditingPrompt(true)}
                    onSave={() => void savePrompt()}
                  />
                ) : undefined
              }
            >
              {/* The prompt is a passage rather than a row, so it is set in from the rule above it
                  and the section under it by the same measure — a paragraph run flush to a hairline
                  reads as the rule's caption instead of as the section's body. */}
              <div className="py-2xl">
                {writable && editingPrompt ? (
                  <Textarea
                    id="agent-prompt"
                    aria-label="Prompt"
                    required
                    value={prompt}
                    onChange={(event) => {
                      promptDirty.current = true;
                      promptSaved.current = null;
                      setPrompt(event.target.value);
                    }}
                  />
                ) : (
                  <Reveal bare>
                    <pre className="m-0 font-sans text-label text-ink-soft whitespace-pre-wrap wrap-anywhere">
                      {ready.agent.prompt}
                    </pre>
                  </Reveal>
                )}
              </div>
            </Group>
            <Group title="Preferences">
              <div>
                <FormFromSchema
                  schema={ready.spec_schema}
                  layout="rows"
                  values={Object.fromEntries(
                    Object.keys(properties).map((key) => [
                      key,
                      values[key] ?? initialSpecValue(properties[key], ready.spec[key]),
                    ]),
                  )}
                  options={{ model: ready.models }}
                  onChange={(key, value) => {
                    settingsDirty.current = true;
                    settingsSaved.current = null;
                    const next = { ...(intended.current ?? values), [key]: value };
                    setValues(next);
                    void save(next);
                  }}
                />
                {ready.deploy.sandbox_internet ? null : (
                  <Hint className="m-0 mt-md">
                    This deploy grants no sandbox public internet — the app setting narrows a
                    capability that is currently off.
                  </Hint>
                )}
              </div>
            </Group>
            <Group title="Details">
              <Facts
                rows={[
                  { label: "Role", value: ready.agent.main ? "Main app" : "App" },
                  {
                    label: "Installations",
                    value: ready.agent.surfaces.length
                      ? ready.agent.surfaces.map(surfaceWord).join(", ")
                      : "None",
                  },
                  { label: "Updated", value: <Moment at={ready.agent.updated_at} /> },
                  {
                    label: "Prompt digest",
                    value: ready.agent.prompt_digest,
                  },
                  ...(ready.audience
                    ? [
                        {
                          label: "Web audience",
                          value: webAudienceLabel(ready.agent.main, ready.audience),
                        },
                      ]
                    : []),
                  {
                    label: "Usage",
                    value: (
                      <a
                        href={workspaceHash("usage")}
                        className="text-inherit no-underline hover:underline focus-visible:underline"
                      >
                        Workspace usage
                      </a>
                    ),
                  },
                ]}
              />
            </Group>

            {ready.agent.archivable ? (
              <Group title="Archive">
                <Hint className="m-0">
                  The app stops and releases its name. Its conversations, tasks, and connected
                  accounts stay.
                </Hint>
                <div className="mt-lg flex justify-end">
                  <ConfirmButton verb="Archive" variant="row" busy={busy} onClick={archive} />
                </div>
              </Group>
            ) : null}
          </>
        );
      }}
    </Panel>
  );
}
