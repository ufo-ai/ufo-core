import { useEffect, useRef, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Facts, Group } from "@/components/ui/facts";
import { Field, Hint, Textarea } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { type NoticeState, OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { AGENT_ICONS, AgentIcon } from "@/lib/agentIcon";
import { postIntent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { newChatHash, workspaceHash } from "@/lib/route";
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

type SettingsPayload = {
  agent: {
    name: string;
    main: boolean;
    surfaces: string[];
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

export function Settings({ agent }: { agent: Agent }) {
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

        async function save(event: FormEvent) {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "apply",
            kind: "agent",
            name: ready.agent.name,
            spec: values,
          });
          setBusy(false);
          setNotice(outcomeNotice(outcome));
          if (outcome.applied) {
            settingsSaved.current = { ...values };
            setReloads((count) => count + 1);
          }
        }

        async function savePrompt(event: FormEvent) {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "apply",
            kind: "agent",
            name: ready.agent.name,
            spec: { prompt },
          });
          setBusy(false);
          setNotice(outcomeNotice(outcome));
          if (outcome.applied) {
            promptSaved.current = prompt;
            setReloads((count) => count + 1);
          }
        }

        async function saveIcon(event: FormEvent) {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          const outcome = await postIntent(agent.id, {
            verb: "apply",
            kind: "agent",
            name: ready.agent.name,
            spec: { icon },
          });
          setBusy(false);
          setNotice(outcomeNotice(outcome));
          if (outcome.applied) {
            iconSaved.current = icon;
            setReloads((count) => count + 1);
          }
        }

        // The setup ask goes to the composer, not to a send. Every grant binds to the agent whose
        // conversation it is made in, and the member who presses this is the speaker the granting
        // verbs gate on — so the member must be *in* that conversation, not merely the cause of
        // one. Founding it here would leave an id nothing else holds: no rail row, a read-only
        // conversation tab, and a connect link that lives only on the live tail.
        function startSetup() {
          setPendingAsk(agent.id, SETUP_ASK, false);
          window.location.hash = newChatHash(agent.id);
        }

        return (
          <>
            <OutcomeNotice state={notice} />
            {ready.agent.setup ? (
              <Group title="Setup">
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
            <Group title="Agent">
              <Facts
                rows={[
                  { label: "Role", value: ready.agent.main ? "Main agent" : "Agent" },
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

            <Group title="Icon">
              {/* The shortlist is drawn whole, so the member picks by eye rather than opening a
                  list of names, and the app's own mark leads it when the agent carries one from
                  outside — a mark the picker did not offer is still the mark it has, and a grid
                  with nothing selected would state otherwise. A mark carries no name the member
                  reads; what a screen reader is given is the mark's own word. */}
              <form onSubmit={saveIcon}>
                <fieldset
                  className={cn(
                    "m-0 grid min-w-0 gap-xs border-0 p-0",
                    "grid-cols-[repeat(auto-fill,minmax(var(--size-touch),1fr))]",
                  )}
                >
                  <legend className="sr-only">Icon</legend>
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
                <div className="mt-lg flex justify-end">
                  <Button type="submit" variant="send" size="bar" busy={busy}>
                    Save icon
                  </Button>
                </div>
              </form>
            </Group>

            <Group title="Settings">
              <form onSubmit={save}>
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
                    setValues((current) => ({ ...current, [key]: value }));
                  }}
                />
                {ready.deploy.sandbox_internet ? null : (
                  <Hint className="m-0 mt-md">
                    This deploy grants no sandbox public internet — the agent setting narrows a
                    capability that is currently off.
                  </Hint>
                )}
                <div className="mt-lg flex justify-end">
                  <Button type="submit" variant="send" size="bar" busy={busy}>
                    Save
                  </Button>
                </div>
              </form>
            </Group>

            <Group title="Prompt">
              {ready.audience !== null && ready.audience !== undefined ? (
                <form onSubmit={savePrompt}>
                  <Field label="Prompt" htmlFor="agent-prompt">
                    <Textarea
                      id="agent-prompt"
                      required
                      value={prompt}
                      onChange={(event) => {
                        promptDirty.current = true;
                        promptSaved.current = null;
                        setPrompt(event.target.value);
                      }}
                    />
                  </Field>
                  <div className="mt-lg flex justify-end">
                    <Button type="submit" variant="send" size="bar" busy={busy}>
                      Save prompt
                    </Button>
                  </div>
                </form>
              ) : (
                <Reveal bare>
                  <pre className="m-0 font-sans text-label text-ink-soft whitespace-pre-wrap wrap-anywhere">
                    {ready.agent.prompt}
                  </pre>
                </Reveal>
              )}
            </Group>
          </>
        );
      }}
    </Panel>
  );
}
