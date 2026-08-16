import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Facts, Group } from "@/components/ui/facts";
import { Field, Hint, Textarea } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { type NoticeState, OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { newChatHash } from "@/lib/route";
import { setPendingAsk } from "@/lib/pendingAsk";
import { webAudienceLabel } from "@/lib/audience";
import { day } from "@/lib/moments";
import type { Agent, SchemaProperty } from "@/lib/types";


/** What an extension-shipped agent still needs granted before it can work, or null once it is
 *  wired. Derived from the grants themselves, so the offer disappears on its own. */
type AgentSetupNeeded = { connectors: string[]; instructions: string };

/** What the button types on the member's behalf. It names the skill rather than the acts, so the
 *  agent reads its own outstanding grants at that moment instead of trusting a stale button. */
const SETUP_ASK = "Load the agent-setup skill and follow its instructions.";

type OverviewPayload = {
  agent: {
    name: string;
    main: boolean;
    surfaces: string[];
    updated_at: string;
    prompt: string;
    prompt_digest: string;
    setup: AgentSetupNeeded | null;
  };
  spec: Record<string, unknown>;
  spec_schema: { properties?: Record<string, SchemaProperty> };
  models: string[];
  deploy: { sandbox_internet: boolean };
  audience?: string[] | null;
};

export function Overview({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<OverviewPayload>("/agents/" + agent.id + "/overview", reloads);
  const [values, setValues] = useState<Record<string, SpecValue>>({});
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsSaved, setSettingsSaved] = useState<Record<string, SpecValue> | null>(null);
  const [prompt, setPrompt] = useState("");
  const [promptDirty, setPromptDirty] = useState(false);
  const [promptSaved, setPromptSaved] = useState<string | null>(null);
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
    const settingsLanded =
      settingsSaved !== null &&
      Object.keys(properties).every((key) => projected[key] === settingsSaved[key]);
    if (!settingsDirty || settingsLanded) {
      setValues(projected);
      if (settingsLanded) {
        setSettingsDirty(false);
        setSettingsSaved(null);
      }
    }
    const promptLanded = promptSaved !== null && payload.agent.prompt === promptSaved;
    if (!promptDirty || promptLanded) {
      setPrompt(payload.agent.prompt);
      if (promptLanded) {
        setPromptDirty(false);
        setPromptSaved(null);
      }
    }
  }, [payload, promptDirty, promptSaved, settingsDirty, settingsSaved]);

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
            setSettingsSaved({ ...values });
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
            setPromptSaved(prompt);
            setReloads((count) => count + 1);
          }
        }

        // The setup ask goes to the composer, not to a send. Every grant binds to the agent whose
        // conversation it is made in, and the member who presses this is the speaker the granting
        // verbs gate on — so the member must be *in* that conversation, not merely the cause of
        // one. Founding it here would leave an id nothing else holds: no rail row, a read-only
        // conversation tab, and a connect link that lives only on the live tail.
        function startSetup() {
          setPendingAsk(agent.id, SETUP_ASK);
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
                    value: ready.agent.surfaces.length ? ready.agent.surfaces.join(", ") : "None",
                  },
                  { label: "Updated", value: day(ready.agent.updated_at) },
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
                ]}
              />
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
                    setSettingsDirty(true);
                    setSettingsSaved(null);
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
                        setPromptDirty(true);
                        setPromptSaved(null);
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
