import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Facts, Group } from "@/components/ui/facts";
import { Hint } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { type NoticeState, OutcomeNotice, Panel, QUIET, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { webAudienceLabel } from "@/lib/audience";
import { day } from "@/lib/moments";
import type { Agent, SchemaProperty } from "@/lib/types";


type OverviewPayload = {
  agent: {
    name: string;
    main: boolean;
    surfaces: string[];
    updated_at: string;
    prompt: string;
    prompt_digest: string;
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
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [busy, setBusy] = useState(false);

  const payload = state.phase === "ready" ? state.payload : null;

  useEffect(() => {
    if (!payload) return;
    const properties = payload.spec_schema.properties ?? {};
    setValues(
      Object.fromEntries(
        Object.keys(properties).map((key) => [
          key,
          initialSpecValue(properties[key], payload.spec[key]),
        ]),
      ),
    );
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
          if (outcome.applied) setReloads((count) => count + 1);
        }

        return (
          <>
            <OutcomeNotice state={notice} />
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
                  onChange={(key, value) => setValues((current) => ({ ...current, [key]: value }))}
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
              <p className="m-0 py-md text-label text-ink-soft">
                Prompt changes go through the governed proposal path in chat.
              </p>
              <Reveal bare>
                <pre className="m-0 font-sans text-label text-ink-soft whitespace-pre-wrap wrap-anywhere">
                  {ready.agent.prompt}
                </pre>
              </Reveal>
            </Group>
          </>
        );
      }}
    </Panel>
  );
}
