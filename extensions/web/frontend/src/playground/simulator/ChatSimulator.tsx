import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
import { resetAppStatusStore } from "@/lib/appStatusStore";
import { resetChatStore, useChat } from "@/lib/chatStore";
import { clearDraft } from "@/lib/drafts";
import { sendMessage, type ChatTarget } from "@/lib/turnStream";
import { Chat, type ChatProps } from "@/views/Chat";
import {
  PACES,
  SCENARIOS,
  SIMULATED_MODEL,
  type Pace,
  type Scenario,
} from "@/playground/simulator/scenarios";
import { armScenario, clearWire, holdPace, installWire } from "@/playground/simulator/wire";

export const SIMULATOR_SLUG = "simulator";
export const SIMULATOR_NAME = "Chat simulator";

const CONVERSATION = "9f1c7a20-0000-4000-8000-0000000000e1";

const AGENT: ChatProps["agent"] = {
  id: "9f1c7a20-0000-4000-8000-0000000000e2",
  name: "assistant",
  model: SIMULATED_MODEL,
  icon: "propylon",
};

const MEMBER: ChatProps["member"] = {
  id: "9f1c7a20-0000-4000-8000-0000000000e3",
  email: "member@example.com",
  admin: false,
};

const TARGET: ChatTarget = {
  key: CONVERSATION,
  agentId: AGENT.id,
  agentModel: AGENT.model,
  conversationId: CONVERSATION,
};

const DRAFT = MEMBER.id + "/" + CONVERSATION;

const STAGE = "flex h-dvh min-h-0 min-w-0 flex-col";
const BAR = "flex flex-wrap items-center gap-sm border-b border-edge bg-surface px-2xl py-sm";
const GROUP = "flex flex-wrap items-center gap-2xs";
const LABEL = "text-label text-ink-quiet";
const THREAD = "flex min-h-0 min-w-0 flex-1 flex-col";

/** The real chat surface on a stubbed wire: every scenario is the frames a turn would stream, so a
 *  state that needs a model, a subagent or a dropped connection to reach is one press away. The
 *  stubs go up before the chat mounts and come down with it — this page is its own entry, so the
 *  portal is never running beside them. */
export function ChatSimulator() {
  const [installed, setInstalled] = useState(false);
  const [pace, setPace] = useState<Pace>("fast");
  const [armed, setArmed] = useState(SCENARIOS[0].id);
  const [generation, setGeneration] = useState(0);
  const state = useChat(CONVERSATION);

  useEffect(() => {
    const restore = installWire();
    armScenario(SCENARIOS[0]);
    setInstalled(true);
    return restore;
  }, []);

  useEffect(() => holdPace(pace), [pace]);

  const start = (scenario: Scenario) => {
    armScenario(scenario);
    setArmed(scenario.id);
    void sendMessage(TARGET, scenario.ask, scenario.ask);
  };

  const reset = () => {
    clearWire();
    resetChatStore();
    resetAppStatusStore();
    clearDraft(DRAFT);
    setGeneration((held) => held + 1);
  };

  return (
    <div className={STAGE}>
      <div className={BAR}>
        <div className={GROUP} role="group" aria-label="Turn">
          <span className={LABEL}>Turn</span>
          {SCENARIOS.map((scenario) => (
            <Button
              key={scenario.id}
              variant="option"
              size="bar"
              aria-pressed={armed === scenario.id}
              disabled={state.busy}
              onClick={() => start(scenario)}
            >
              {scenario.name}
            </Button>
          ))}
        </div>
        <div className={GROUP} role="group" aria-label="Pace">
          <span className={LABEL}>Pace</span>
          {PACES.map((option) => (
            <Button
              key={option.pace}
              variant="option"
              size="bar"
              aria-pressed={pace === option.pace}
              onClick={() => setPace(option.pace)}
            >
              {option.label}
            </Button>
          ))}
        </div>
        <Button variant="outline" size="bar" onClick={reset}>
          Reset
        </Button>
      </div>
      <div className={THREAD}>
        {installed ? (
          <TooltipProvider>
            <Chat
              key={generation}
              agent={AGENT}
              member={MEMBER}
              conversationId={CONVERSATION}
              focusComposer
            />
          </TooltipProvider>
        ) : null}
      </div>
    </div>
  );
}
