import { useState } from "react";

import { SILENT, Toast, type ToastState } from "@/components/ui/toast";
import { Panel, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { chatSurface, useAgents, useMainAgent, useRereadAgents } from "@/lib/mainAgent";
import type { Agent } from "@/lib/types";
import { SpecPreferences, type SettingsPayload } from "@/views/Preferences";

/** The spec fields a chat runs on, in the order a member reads them. A deploy whose schema omits one
 *  — a single-shape sandbox backend has no size — draws the rest. */
const CHAT_FIELDS = ["model", "reasoning", "sandbox_size", "use_workspace_skills"];

/** The workspace's chat defaults: the settings of the app a new chat opens against — the chat app
 *  where the workspace holds one, the main agent otherwise. */
export function ChatDefaults() {
  const agents = useAgents();
  const main = useMainAgent();
  const agent = chatSurface(agents) ?? main;
  if (!agent) return <PanelEmpty>This workspace holds no app that answers chat.</PanelEmpty>;
  return <Defaults agent={agent} />;
}

function Defaults({ agent }: { agent: Agent }) {
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<SettingsPayload>("/agents/" + agent.id + "/settings", reloads);
  const [toast, setToast] = useState<ToastState>(SILENT);
  const reread = useRereadAgents();
  return (
    <Section note={"Every chat " + agentName(agent.name) + " answers starts on these."}>
      <Toast state={toast} onDone={() => setToast(SILENT)} position="surface" />
      <Panel state={state} shape="form">
        {(ready) => (
          <SpecPreferences
            agentId={agent.id}
            payload={ready}
            fields={CHAT_FIELDS.filter((field) =>
              Object.hasOwn(ready.spec_schema.properties ?? {}, field),
            )}
            onSettled={(applied) => {
              if (applied) {
                setToast({ title: "Chat defaults saved." });
                reread?.();
              }
              setReloads((count) => count + 1);
            }}
          />
        )}
      </Panel>
    </Section>
  );
}
