import { Hint } from "@/components/ui/field";
import { Table, Td, Th } from "@/components/ui/table";
import { Panel, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { TurnLine, turnTree, who, type Turn } from "@/views/Conversations";
import { day } from "@/lib/moments";
import { Heading } from "@/views/Usage";
import { cn } from "@/lib/cn";
import { subagentConversationHash, subagentHash, type SubagentTab } from "@/lib/route";
import type { Subagent } from "@/lib/types";

const TAB_LABELS: Record<SubagentTab, string> = {
  overview: "Overview",
  conversations: "Conversations",
  skills: "Skills",
};

type Detail = {
  name: string;
  model: string | null;
  prompt: string;
  max_rounds: number;
  untrusted_output: boolean;
  loads_skills: boolean;
};

type Run = {
  id: string;
  agent_name: string;
  member_email: string | null;
  turn_count: number;
  last_turn_at: string;
  readable: boolean;
};

export type SubagentPaneProps = {
  subagent: Subagent;
  tab: SubagentTab;
  tabs: readonly SubagentTab[];
  onTab: (tab: SubagentTab) => void;
  conversationId?: string;
};

export function SubagentPane({ subagent, tab, tabs, onTab, conversationId }: SubagentPaneProps) {
  const base = "/subagents/" + subagent.name;
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md px-2xl pt-lg">
        <h1 className="m-0 text-title font-strong">{subagent.name}</h1>
        <span className="font-mono text-mono opacity-(--muted-strong)">subagent</span>
      </div>
      <div role="tablist" className="flex gap-2xs border-b border-edge px-lg pt-xs">
        {tabs.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={name === tab}
            onClick={() => onTab(name)}
            className={cn(
              "border-0 border-b-(length:--marker-width) border-b-transparent bg-transparent",
              "px-md py-xs text-inherit opacity-(--muted-soft)",
              name === tab && "border-b-ink font-strong opacity-100",
            )}
          >
            {TAB_LABELS[name]}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-2xl" data-testid="panel">
        {tab === "overview" ? <SubagentOverview base={base} /> : null}
        {tab === "conversations" ? (
          <SubagentConversations
            base={base}
            name={subagent.name}
            conversationId={conversationId}
          />
        ) : null}
        {tab === "skills" ? <SubagentSkills base={base} /> : null}
      </div>
    </main>
  );
}

function SubagentOverview({ base }: { base: string }) {
  const state = usePanelRead<{ subagent: Detail }>(base + "/overview");
  return (
    <Panel state={state}>
      {({ subagent }) => (
        <>
          <Section title="Subagent">
            <div className="font-mono text-mono">
              {[
                subagent.model
                  ? "model: " + subagent.model
                  : "model: inherits the agent that spawns it",
                "round limit: " + subagent.max_rounds,
                subagent.untrusted_output
                  ? "its answer reaches a parent walled as untrusted content"
                  : "its answer reaches a parent as trusted",
              ].join(" · ")}
            </div>
          </Section>

          <Section title="Prompt">
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-panel bg-fill-subtle p-lg font-mono text-mono">
              {subagent.prompt}
            </pre>
          </Section>
        </>
      )}
    </Panel>
  );
}

function SubagentSkills({ base }: { base: string }) {
  const state = usePanelRead<{ loads_skills: boolean; skills: { name: string; description: string }[] }>(
    base + "/skills",
  );
  return (
    <Panel
      state={state}
      empty={(payload) =>
        payload.loads_skills ? null : "This subagent holds no load_skill tool, so it loads no skills."
      }
    >
      {(payload) => (
        <>
          <h2 className="mb-2xs mt-xl text-label opacity-(--muted-soft)">Deploy skills</h2>
          <Hint>
            A child also loads the member-authored skills of the agent that spawned it, listed on
            that agent's own skills panel.
          </Hint>
          <Table>
            <thead>
              <tr>
                {["name", "description"].map((column) => (
                  <Th key={column}>{column}</Th>
                ))}
              </tr>
            </thead>
            <tbody>
              {payload.skills.map((skill) => (
                <tr key={skill.name}>
                  <Td>{skill.name}</Td>
                  <Td>{skill.description}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </Panel>
  );
}

function SubagentConversations({
  base,
  name,
  conversationId,
}: {
  base: string;
  name: string;
  conversationId?: string;
}) {
  const state = usePanelRead<{ conversations: Run[] }>(
    conversationId ? null : base + "/conversations",
  );

  if (conversationId) {
    return <RunDetail base={base} name={name} conversationId={conversationId} />;
  }
  return (
    <Panel
      state={state}
      empty={(payload) => (payload.conversations.length ? null : "This subagent has not run yet.")}
    >
      {(payload) => (
        <Table>
          <thead>
            <tr>
              {["agent", "asked by", "turns", "last activity", ""].map((column, index) => (
                <Th key={index}>{column}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {payload.conversations.map((run) => (
              <tr key={run.id}>
                <Td>{run.agent_name}</Td>
                <Td>{who(run)}</Td>
                <Td>{String(run.turn_count)}</Td>
                <Td>{day(run.last_turn_at)}</Td>
                <Td>
                  {run.readable ? (
                    <a href={subagentConversationHash(name, run.id)}>Open</a>
                  ) : (
                    <span className="font-mono text-mono">not shared with you</span>
                  )}
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Panel>
  );
}

function RunDetail({
  base,
  name,
  conversationId,
}: {
  base: string;
  name: string;
  conversationId: string;
}) {
  const state = usePanelRead<{ run: Run; turns: Turn[]; subagent_turns: Turn[] }>(
    base + "/conversations/" + conversationId,
  );
  return (
    <>
      <a href={subagentHash(name, "conversations")}>All conversations</a>
      <Panel
        state={state}
        failed={(message) => (
          <PanelEmpty>
            {message.startsWith("Error 404") ? "This conversation is not shared with you." : message}
          </PanelEmpty>
        )}
        empty={(payload) =>
          payload.turns.length || payload.subagent_turns.length ? null : "No turns in this run yet."
        }
      >
        {(payload) => (
          <>
            <Heading>
              {payload.run.agent_name} · {who(payload.run)}
            </Heading>
            <div className="my-lg flex flex-col gap-lg">
              {turnTree(payload.turns, payload.subagent_turns).map((entry) => (
                <TurnLine
                  key={entry.turn.id}
                  turn={entry.turn}
                  depth={entry.depth}
                  showChanges={entry.first}
                  rootConversationId={
                    entry.turn.conversation_id === conversationId ? undefined : conversationId
                  }
                />
              ))}
            </div>
          </>
        )}
      </Panel>
    </>
  );
}
