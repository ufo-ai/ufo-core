import { useEffect, useRef, useState } from "react";

import { IconPencil } from "@tabler/icons-react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts, Group } from "@/components/ui/facts";
import { Hint, Textarea } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { type NoticeState, OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { AGENT_ICONS, AgentIcon } from "@/lib/agentIcon";
import { agentName } from "@/lib/agentName";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { workspaceHash } from "@/lib/route";
import { surfaceWord, webAudienceLabel } from "@/lib/audience";
import { Moment } from "@/lib/moments";
import type { Agent } from "@/lib/types";
import { SpecPreferences, type SettingsPayload } from "@/views/Preferences";


const UFO_ICON = "ufo";

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

export function Settings({ agent, onArchived }: { agent: Agent; onArchived: () => void }) {
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<SettingsPayload>("/agents/" + agent.id + "/settings", reloads);
  const [prompt, setPrompt] = useState("");
  const [icon, setIcon] = useState("");
  const promptDirty = useRef(false);
  const promptSaved = useRef<string | null>(null);
  const iconDirty = useRef(false);
  const iconSaved = useRef<string | null>(null);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);
  const [editingIcon, setEditingIcon] = useState(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [toast, setToast] = useState<ToastState>(SILENT);

  const payload = state.phase === "ready" ? state.payload : null;

  useEffect(() => {
    if (!payload) return;
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
        const writable = ready.audience !== null && ready.audience !== undefined;

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

        return (
          <>
            <Toast state={toast} onDone={() => setToast(SILENT)} position="surface" />
            <OutcomeNotice state={notice} />
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
              <SpecPreferences
                agentId={agent.id}
                payload={ready}
                onSettled={(applied) => {
                  if (applied) setToast({ title: agentName(ready.agent.name) + " saved." });
                  setReloads((count) => count + 1);
                }}
              />
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
