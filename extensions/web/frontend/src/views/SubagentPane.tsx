import { useState } from "react";

import { Button, buttonVariants } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Hint, Input } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { Table, Td, Th } from "@/components/ui/table";
import { Panel, PanelBlank, PanelEmpty, Section, usePanelRead } from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { cn } from "@/lib/cn";
import { TabPanel, TabStrip } from "@/kernel/tabs";
import { TurnLine, turnTree, who, type Turn } from "@/views/Conversations";
import { day } from "@/lib/moments";
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
        <span className="opacity-(--muted-strong)">subagent</span>
      </div>
      <TabStrip
        group="subagent"
        tabs={tabs}
        current={tab}
        label={(name) => TAB_LABELS[name]}
        onPick={onTab}
      />
      <TabPanel
        group="subagent"
        current={tab}
        className="flex-1 overflow-y-auto p-2xl"
        data-testid="panel"
      >
        {tab === "overview" ? <SubagentOverview base={base} /> : null}
        {tab === "conversations" ? (
          <SubagentConversations
            base={base}
            name={subagent.name}
            conversationId={conversationId}
          />
        ) : null}
        {tab === "skills" ? <SubagentSkills base={base} /> : null}
      </TabPanel>
    </main>
  );
}

const MONO = "font-mono text-mono";

function SubagentOverview({ base }: { base: string }) {
  const state = usePanelRead<{ subagent: Detail }>(base + "/overview");
  return (
    <Panel state={state} shape="form">
      {({ subagent }) => (
        <>
          <Section title="Subagent">
            <Facts
              rows={[
                {
                  label: "Model",
                  value: subagent.model ? (
                    <span className={MONO}>{subagent.model}</span>
                  ) : (
                    "Inherits the agent that spawns it"
                  ),
                },
                { label: "Round limit", value: String(subagent.max_rounds) },
                {
                  label: "Answer to parent",
                  value: subagent.untrusted_output
                    ? "Walled as untrusted content"
                    : "Trusted content",
                },
              ]}
            />
          </Section>

          <Section title="Prompt">
            <Reveal>
              <pre className={cn("m-0 whitespace-pre-wrap wrap-anywhere", MONO)}>
                {subagent.prompt}
              </pre>
            </Reveal>
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
    <Panel state={state}>
      {(payload) => (
        <Section title="Deploy skills">
          {payload.loads_skills ? (
            <Hint className="m-0">
              A child also loads the member-authored skills of the agent that spawned it, listed
              under Customize for that agent.
            </Hint>
          ) : null}
          {payload.loads_skills && payload.skills.length ? (
            <Table>
              <thead>
                <tr>
                  {["Name", "Description"].map((column) => (
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
          ) : (
            <PanelBlank
              body={
                payload.loads_skills
                  ? "This deploy declares no skills for this subagent."
                  : "This subagent holds no load_skill tool, so it loads no skills."
              }
            />
          )}
        </Section>
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
  const [query, setQuery] = useState("");
  const [reloads, setReloads] = useState(0);
  const state = usePanelRead<{ conversations: Run[] }>(
    conversationId ? null : base + "/conversations",
    reloads,
  );

  if (conversationId) {
    return <RunDetail base={base} name={name} conversationId={conversationId} />;
  }
  return (
    <Panel state={state}>
      {(payload) => (
        <Section
          title="Runs"
          bar={
            <>
              <Input
                type="search"
                aria-label="Search"
                placeholder="Search"
                className="max-w-control-row"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <Button onClick={() => setReloads((count) => count + 1)}>Refresh</Button>
            </>
          }
        >
          <DataTable
            columns={["Agent", "Asked By", "Turns", "Last Activity", ""]}
            rows={payload.conversations.filter((run) =>
              (run.agent_name + " " + who(run)).toLowerCase().includes(query.toLowerCase()),
            )}
            rowKey={(run) => run.id}
            empty="This subagent has not run yet."
            note={query ? "No run matches this search." : undefined}
          >
            {(run) => (
              <>
                <Td>{run.agent_name}</Td>
                <Td>{who(run)}</Td>
                <Td>{String(run.turn_count)}</Td>
                <Td>{day(run.last_turn_at)}</Td>
                <Td>
                  {run.readable ? (
                    <a
                      href={subagentConversationHash(name, run.id)}
                      className={cn(
                        buttonVariants({ variant: "row" }),
                        "inline-block no-underline",
                      )}
                    >
                      Open
                    </a>
                  ) : (
                    <span className="opacity-(--muted-soft)">not shared with you</span>
                  )}
                </Td>
              </>
            )}
          </DataTable>
        </Section>
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
      <div className="mb-lg">
        <a
          href={subagentHash(name, "conversations")}
          className={cn(buttonVariants({ variant: "row" }), "inline-block no-underline")}
        >
          All conversations
        </a>
      </div>
      <Panel
        state={state}
        failed={(message) => (
          <PanelEmpty>
            {message.startsWith("Error 404") ? "This conversation is not shared with you." : message}
          </PanelEmpty>
        )}
      >
        {(payload) => (
          <Section title={payload.run.agent_name + " · " + who(payload.run)}>
            {payload.turns.length || payload.subagent_turns.length ? (
              <div className="flex flex-col gap-lg">
                {turnTree(payload.turns, payload.subagent_turns).map((entry) => (
                  <TurnLine key={entry.turn.id} turn={entry.turn} depth={entry.depth} />
                ))}
              </div>
            ) : (
              <PanelBlank body="No turns in this run yet." />
            )}
          </Section>
        )}
      </Panel>
    </>
  );
}
