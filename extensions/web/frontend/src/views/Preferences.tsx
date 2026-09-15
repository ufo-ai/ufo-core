import { useEffect, useRef, useState } from "react";

import { Hint } from "@/components/ui/field";
import { FormFromSchema, initialSpecValue, type SpecValue } from "@/kernel/form";
import { OutcomeNotice, QUIET, outcomeNotice, type NoticeState } from "@/kernel/panel";
import { postIntent } from "@/lib/api";
import type { SchemaProperty } from "@/lib/types";

export type SettingsPayload = {
  agent: {
    name: string;
    main: boolean;
    surfaces: string[];
    archivable: boolean;
    updated_at: string;
    prompt: string;
    prompt_digest: string;
  };
  spec: { icon: string; [field: string]: unknown };
  spec_schema: { properties?: Record<string, SchemaProperty> };
  models: string[];
  deploy: { sandbox_internet: boolean };
  audience?: string[] | null;
};

/** The agent spec's own controls, drawn wherever a member sets what an agent runs on: the app's
 *  settings pane and the workspace's chat defaults. Each change is sent as it is made, one intent at
 *  a time, and the panel re-reads itself through `onSettled` so a refusal puts the stored values
 *  back. */
export function SpecPreferences({
  agentId,
  payload,
  fields,
  onSettled,
}: {
  agentId: string;
  payload: SettingsPayload;
  fields?: string[];
  onSettled: (applied: boolean) => void;
}) {
  const [values, setValues] = useState<Record<string, SpecValue>>({});
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const dirty = useRef(false);
  const sentSpec = useRef<Record<string, SpecValue> | null>(null);
  const intended = useRef<Record<string, SpecValue> | null>(null);
  const saving = useRef(false);
  const properties = payload.spec_schema.properties ?? {};
  const shown = fields ?? Object.keys(properties);

  useEffect(() => {
    const projected = Object.fromEntries(
      Object.keys(properties).map((key) => [
        key,
        initialSpecValue(properties[key], payload.spec[key]),
      ]),
    );
    const sent = sentSpec.current;
    const landed =
      sent !== null && Object.keys(properties).every((key) => projected[key] === sent[key]);
    if (!dirty.current || landed) {
      setValues(projected);
      if (landed) {
        dirty.current = false;
        sentSpec.current = null;
        intended.current = null;
      }
    }
  }, [payload]);

  async function save(spec: Record<string, SpecValue>) {
    intended.current = spec;
    if (saving.current) return;
    saving.current = true;
    let sent: Record<string, SpecValue> | null = null;
    while (intended.current !== null && intended.current !== sent) {
      sent = intended.current;
      const outcome = await postIntent(agentId, {
        verb: "apply",
        kind: "agent",
        name: payload.agent.name,
        spec: sent,
      });
      if (!outcome.applied) {
        intended.current = null;
        dirty.current = false;
        sentSpec.current = null;
        saving.current = false;
        setNotice(outcomeNotice(outcome));
        onSettled(false);
        return;
      }
      setNotice(QUIET);
      sentSpec.current = { ...sent };
    }
    saving.current = false;
    onSettled(true);
  }

  return (
    <div>
      <OutcomeNotice state={notice} />
      <FormFromSchema
        schema={payload.spec_schema}
        layout="rows"
        fields={shown}
        values={Object.fromEntries(
          Object.keys(properties).map((key) => [
            key,
            values[key] ?? initialSpecValue(properties[key], payload.spec[key]),
          ]),
        )}
        options={{ model: payload.models }}
        onChange={(key, value) => {
          dirty.current = true;
          sentSpec.current = null;
          const next = { ...(intended.current ?? values), [key]: value };
          setValues(next);
          void save(next);
        }}
      />
      {payload.deploy.sandbox_internet || !shown.includes("internet_access_allowed") ? null : (
        <Hint className="m-0 mt-md">
          This deploy grants no sandbox public internet — the app setting narrows a capability that
          is currently off.
        </Hint>
      )}
    </div>
  );
}
