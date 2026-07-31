import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Hint } from "@/components/ui/field";
import { SpecField, initialSpecValue, type SpecValue } from "@/kernel/form";
import { Notice, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
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
  const [notice, setNotice] = useState("");
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

  if (state.phase === "loading") return null;
  if (state.phase === "failed") return <PanelEmpty>{state.message}</PanelEmpty>;

  const data = state.payload;
  const properties = data.spec_schema.properties ?? {};

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const outcome = await postIntent(agent.id, {
      verb: "apply",
      kind: "agent",
      name: data.agent.name,
      spec: values,
    });
    setBusy(false);
    setNotice(outcome.message);
    if (outcome.applied) setReloads((count) => count + 1);
  }

  return (
    <>
      <Section title="Agent">
        <div className="font-mono text-mono">
          {[
            data.agent.main ? "main agent" : "agent",
            "installations: " +
              (data.agent.surfaces.length ? data.agent.surfaces.join(", ") : "none"),
            "updated " + data.agent.updated_at.slice(0, 16).replace("T", " "),
          ].join(" · ")}
        </div>
      </Section>

      <Section title="Settings">
        <form onSubmit={save}>
          {Object.keys(properties).map((key) => (
            <SpecField
              key={key}
              name={key}
              prop={properties[key]}
              value={values[key] ?? initialSpecValue(properties[key], data.spec[key])}
              options={key === "model" ? data.models : null}
              onChange={(value) => setValues((current) => ({ ...current, [key]: value }))}
            />
          ))}
          {data.deploy.sandbox_internet ? null : (
            <Hint>
              This deploy grants no sandbox public internet — the agent setting narrows a capability
              that is currently off.
            </Hint>
          )}
          <Button type="submit" variant="send" disabled={busy}>
            Save
          </Button>
          <Notice>{notice}</Notice>
        </form>
      </Section>

      <Section title="Prompt">
        <Hint className="font-mono">
          digest {data.agent.prompt_digest} — prompt changes go through the governed proposal path
          in chat
        </Hint>
        <pre className="overflow-x-auto whitespace-pre-wrap rounded-panel bg-fill-subtle p-lg font-mono text-mono">
          {data.agent.prompt}
        </pre>
      </Section>

      {data.audience ? (
        <Section title="Web audience">
          <div className="font-mono text-mono">
            {data.agent.main
              ? "every member"
              : data.audience.length
                ? data.audience.join(", ")
                : "no member grants — admins only"}
          </div>
        </Section>
      ) : null}
    </>
  );
}
