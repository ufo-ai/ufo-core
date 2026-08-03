import { Chat, type ChatProps } from "@/views/Chat";

export type ChatPaneProps = ChatProps & { onOpenAgent: (agentId: string) => void };

export function ChatPane({
  agent,
  member,
  conversationId,
  onCreated,
  onActivity,
  onOpenAgent,
}: ChatPaneProps) {
  return (
    <main className="flex min-h-0 min-w-0 flex-col">
      <div className="flex items-baseline gap-md border-b border-edge px-2xl py-lg">
        <button
          type="button"
          onClick={() => onOpenAgent(agent.id)}
          className="m-0 border-0 bg-transparent p-0 text-title font-strong text-inherit"
        >
          {agent.name}
        </button>
        <span className="font-mono text-mono opacity-(--muted-strong)">{agent.model}</span>
      </div>
      <Chat
        agent={agent}
        member={member}
        conversationId={conversationId}
        onCreated={onCreated}
        onActivity={onActivity}
      />
    </main>
  );
}
