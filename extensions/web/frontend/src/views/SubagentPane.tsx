import { buttonVariants } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Hint } from "@/components/ui/field";
import { Reveal } from "@/components/ui/reveal";
import { Table, Td, Th } from "@/components/ui/table";
import { Panel, PanelBlank, Section, usePanelRead } from "@/kernel/panel";
import { useViewer } from "@/lib/audience";
import { cn } from "@/lib/cn";
import { TabPanel, TabStrip } from "@/kernel/tabs";
import {
  ConversationList,
  ConversationTranscript,
  conversationTitle,
  unreadable,
} from "@/views/Conversations";
import { subagentConversationHash, subagentHash, type SubagentTab } from "@/lib/route";
import type { Conversation, Message, Subagent } from "@/lib/types";

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

export type SubagentPaneProps = {
  subagent: Subagent;
  tab: SubagentTab;
  tabs: readonly SubagentTab[];
  onTab: (tab: SubagentTab) => void;
  conversationId?: string;
  rootConversationId?: string;
};

export function SubagentPane({
  subagent,
  tab,
  tabs,
  onTab,
  conversationId,
  rootConversationId,
}: SubagentPaneProps) {
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
            rootConversationId={rootConversationId}
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
              under that agent's Skills tab.
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
  rootConversationId,
}: {
  base: string;
  name: string;
  conversationId?: string;
  rootConversationId?: string;
}) {
  const state = usePanelRead<{ conversations: Conversation[] }>(
    conversationId ? null : base + "/conversations",
  );

  if (conversationId) {
    return (
      <RunDetail
        base={base}
        name={name}
        conversationId={conversationId}
        rootConversationId={rootConversationId}
      />
    );
  }
  return (
    <Panel state={state}>
      {(payload) => (
        <ConversationList
          rows={payload.conversations}
          blank="No conversation this subagent ran is shared with you."
          onOpen={(conversation) => {
            location.hash = subagentConversationHash(name, conversation.id);
          }}
        />
      )}
    </Panel>
  );
}

function RunDetail({
  base,
  name,
  conversationId,
  rootConversationId,
}: {
  base: string;
  name: string;
  conversationId: string;
  rootConversationId?: string;
}) {
  const root = rootConversationId ? "?root=" + rootConversationId : "";
  const state = usePanelRead<{ run: Conversation; messages: Message[] }>(
    base + "/conversations/" + conversationId + root,
  );
  const viewer = useViewer();
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
      <Panel state={state} failed={unreadable}>
        {(payload) => (
          <ConversationTranscript
            conversationId={conversationId}
            title={conversationTitle(payload.run, viewer)}
            messages={payload.messages}
          />
        )}
      </Panel>
    </>
  );
}
