import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { FieldGroup, Hint } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { type NoticeState, OutcomeNotice, Panel, QUIET, Section, outcomeNotice, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import { webAudienceLabel } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { day } from "@/lib/moments";
import type { Agent, SchemaProperty } from "@/lib/types";

const MONO = "font-mono text-mono";

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
            <Section title="Agent">
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
                    value: (
                      <span className={cn("wrap-anywhere", MONO)}>{ready.agent.prompt_digest}</span>
                    ),
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
            </Section>

            <Section title="Settings">
              <FieldGroup
                onSubmit={save}
                submit={
                  <Button type="submit" variant="send" busy={busy}>
                    Save
                  </Button>
                }
              >
                <FormFromSchema
                  schema={ready.spec_schema}
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
                  <Hint className="m-0">
                    This deploy grants no sandbox public internet — the agent setting narrows a
                    capability that is currently off.
                  </Hint>
                )}
              </FieldGroup>
            </Section>

            <Section title="Prompt">
              <Hint className="m-0">
                Prompt changes go through the governed proposal path in chat.
              </Hint>
              <Reveal>
                <pre className={cn("m-0 whitespace-pre-wrap wrap-anywhere", MONO)}>
                  {ready.agent.prompt}
                </pre>
              </Reveal>
            </Section>
          </>
        );
      }}
    </Panel>
  );
}
