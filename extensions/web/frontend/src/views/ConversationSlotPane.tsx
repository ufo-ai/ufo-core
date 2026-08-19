import { useState } from "react";

import { ArtifactText, isTextMedia } from "@/kernel/artifact";
import { COLUMN, Pane } from "@/kernel/pane";
import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { agentName } from "@/lib/agentName";
import { cn } from "@/lib/cn";
import { Markdown } from "@/lib/markdown";
import { Moment } from "@/lib/moments";
import type { ConversationAgent } from "@/lib/types";
import { formatSize } from "@/lib/size";

/** A word is the whole of some of these controls — a file name, `Open site`, the way out of the
 *  pane — and a word set in a 13px line is 18px tall. At a phone width each keeps the control
 *  height as the box a finger has to land on, which the words themselves do not change. */
const TAP_FLOOR = "max-narrow:inline-flex max-narrow:min-h-(--size-control) max-narrow:items-center";

export type ConversationSlotSummary = {
  id: string;
  label: string;
  icon: PortalIcon;
  kind: string;
  count: number;
};

export type ConversationSlotsPayload = { slots: ConversationSlotSummary[] };

type Change = { path: string; patch: string; truncated: boolean };
type ChangesPayload = { type: "changes"; changes: Change[]; truncated: boolean };
type ConversationArtifact = {
  filename: string;
  subject: string | null;
  media_type: string;
  size_bytes: number;
  created_at: string;
  url: string | null;
  preview: ImagePreview | null;
};
type ArtifactsPayload = {
  type: "artifacts";
  artifacts: ConversationArtifact[];
  truncated: boolean;
};
type ImagePreview = {
  type: "image";
  media_type: "image/gif" | "image/jpeg" | "image/png" | "image/webp";
  url: string;
};
export type PortalIcon = "artifact" | "calendar" | "diff" | "link" | "task";
type Source = { url: string; title: string; snippet: string; published_date: string | null };
type SourcesPayload = { type: "sources"; sources: Source[]; truncated: boolean };
type TaskStatus = "pending" | "in_progress" | "completed";
type ConversationTask = { description: string; status: TaskStatus };
/** The todo board one conversation holds, as its `tasks` slot answers it. Read here as the slot's
 *  own list, and by the app-building wizard as the progress of its run. */
export type TasksSlotPayload = {
  type: "tasks";
  title: string;
  tasks: ConversationTask[];
  total_count: number;
  completed_count: number;
  truncated: boolean;
};
type Site = {
  name: string;
  url: string;
  visibility: "private" | "workspace" | "public";
  created_at: string;
  updated_at: string;
};
type SitesPayload = { type: "sites"; sites: Site[]; truncated: boolean };
type Automation = {
  name: string;
  description: string | null;
  schedule: string;
  paused: boolean;
  next_run_at: string;
  last_run_at: string | null;
  latest_status: string | null;
  latest_response: string | null;
  created_at: string;
  updated_at: string;
};
type AutomationsPayload = {
  type: "automations";
  automations: Automation[];
  truncated: boolean;
};
type SlotPayload =
  | ArtifactsPayload
  | AutomationsPayload
  | ChangesPayload
  | SitesPayload
  | SourcesPayload
  | TasksSlotPayload;

function slotPath(
  agentId: string,
  conversationId: string,
  slot: string,
  rootConversationId?: string,
) {
  return (
    "/agents/" +
    agentId +
    "/conversations/" +
    conversationId +
    "/slots/" +
    slot +
    (rootConversationId ? "?root=" + rootConversationId : "")
  );
}

function slotsPath(agentId: string, conversationId: string, rootConversationId?: string) {
  return (
    "/agents/" +
    agentId +
    "/conversations/" +
    conversationId +
    "/slots" +
    (rootConversationId ? "?root=" + rootConversationId : "")
  );
}

export function ConversationSlotPane({
  agent,
  conversationId,
  slot,
  rootConversationId,
  summary,
  embedded = false,
  onClose,
  onOpenAgent,
}: {
  agent: ConversationAgent;
  conversationId: string;
  slot: string;
  rootConversationId?: string;
  summary?: ConversationSlotSummary;
  embedded?: boolean;
  onClose?: () => void;
  onOpenAgent?: (agentId: string) => void;
}) {
  const inventory = usePanelRead<ConversationSlotsPayload>(
    summary ? null : slotsPath(agent.id, conversationId, rootConversationId),
  );
  const state = usePanelRead<SlotPayload>(
    slotPath(agent.id, conversationId, slot, rootConversationId),
  );
  const resolved =
    summary ??
    (inventory.phase === "ready"
      ? inventory.payload.slots.find((entry) => entry.id === slot)
      : undefined);
  const label = resolved?.label ?? slot;
  const content = (
    <>
      <div className="flex items-baseline gap-md border-b border-edge px-xl py-lg">
        {!embedded && onOpenAgent ? (
          <button
            type="button"
            onClick={() => onOpenAgent(agent.id)}
            className="m-0 border-0 bg-transparent p-0 text-title font-strong text-inherit"
          >
            {agentName(agent.name)}
          </button>
        ) : null}
        <span className="inline-flex items-center gap-xs font-mono text-mono text-ink-soft">
          {resolved ? <SlotIcon icon={resolved.icon} /> : null}
          {label}
        </span>
        {onClose ? (
          <button type="button" aria-label="Close slot" onClick={onClose} className={TAP_FLOOR}>
            Close
          </button>
        ) : null}
      </div>
      <div className="flex-1 overflow-y-auto scrollbar-gutter-stable p-xl">
        <Panel
          state={state}
          failed={(message) => (
            <PanelEmpty>
              {message.startsWith("Error 404")
                ? "This conversation is not shared with you."
                : message}
            </PanelEmpty>
          )}
        >
          {(payload) => <SlotContent payload={payload} />}
        </Panel>
      </div>
    </>
  );
  if (embedded) {
    return (
      <aside
        className="flex min-h-0 min-w-0 flex-col border-l border-edge max-narrow:absolute max-narrow:inset-0 max-narrow:z-10 max-narrow:border-l-0 max-narrow:bg-surface"
        aria-label={label}
      >
        {content}
      </aside>
    );
  }
  return <Pane className={COLUMN}>{content}</Pane>;
}

export function SlotIcon({ icon }: { icon: PortalIcon }) {
  const path = {
    artifact: "M4 7 12 3l8 4-8 4-8-4Zm0 5 8 4 8-4M4 17l8 4 8-4",
    calendar: "M6 3v4M18 3v4M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z",
    diff: "M8 5v6M5 8h6M14 8h5M5 17h6M14 17h5",
    link: "M9 15l6-6M7.5 17.5l-1 1a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 5 0M16.5 6.5l1-1a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-5 0",
    task: "M9 6h11M9 12h11M9 18h11M3.5 6l1 1 2-2M3.5 12l1 1 2-2M3.5 18l1 1 2-2",
  }[icon];
  return (
    <svg
      aria-hidden
      data-slot-icon={icon}
      viewBox="0 0 24 24"
      className="size-(--size-icon) shrink-0 fill-none stroke-current"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={path} />
    </svg>
  );
}

function SlotContent({ payload }: { payload: SlotPayload }) {
  if (payload.type === "changes") {
    if (!payload.changes.length && !payload.truncated) return <PanelEmpty>No changes.</PanelEmpty>;
    return (
      <div className="flex flex-col gap-xl">
        {payload.changes.map((change, index) => (
          <ChangedFile key={[change.path, index].join(":")} change={change} />
        ))}
        {payload.truncated ? (
          <p className="m-0 text-ink-soft">Some changes may not be shown.</p>
        ) : null}
      </div>
    );
  }
  if (payload.type === "artifacts") return <ArtifactsContent payload={payload} />;
  if (payload.type === "tasks") return <TasksContent payload={payload} />;
  if (payload.type === "automations") return <AutomationsContent payload={payload} />;
  if (payload.type === "sites") return <SitesContent payload={payload} />;
  if (!payload.sources.length && !payload.truncated) return <PanelEmpty>No sources.</PanelEmpty>;
  return (
    <div className="flex flex-col gap-xl">
      {payload.sources.map((source) => (
        <article key={source.url} className="min-w-0 rounded-panel border border-edge p-lg">
          <h2 className="m-0 font-sans text-label font-strong">
            <a href={source.url} target="_blank" rel="noreferrer">
              {source.title || source.url}
            </a>
          </h2>
          {source.published_date ? (
            <p className="m-0 mt-xs font-mono text-mono text-ink-soft">
              {source.published_date}
            </p>
          ) : null}
          {source.snippet ? (
            <details className="mt-sm">
              <summary className="cursor-pointer select-none text-label text-ink-soft">
                Summary
              </summary>
              <div className="mt-sm break-words">
                <Markdown text={source.snippet} />
              </div>
            </details>
          ) : null}
        </article>
      ))}
      {payload.truncated ? (
        <p className="m-0 text-ink-soft">Some sources may not be shown.</p>
      ) : null}
    </div>
  );
}

function TasksContent({ payload }: { payload: TasksSlotPayload }) {
  const labels: Record<TaskStatus, string> = {
    pending: "Pending",
    in_progress: "In progress",
    completed: "Completed",
  };
  return (
    <section className="min-w-0">
      {payload.title ? (
        <h2 className="m-0 font-sans text-title font-strong">{payload.title}</h2>
      ) : null}
      <p className="m-0 mt-sm font-mono text-mono text-ink-soft">
        {payload.completed_count} of {payload.total_count} completed.
      </p>
      {payload.tasks.length ? (
        <ul className="m-0 mt-lg flex list-none flex-col gap-sm p-0">
          {payload.tasks.map((task, index) => (
            <li
              key={[task.description, index].join(":")}
              className="flex min-w-0 items-start gap-md rounded-panel border border-edge p-lg"
            >
              <span className="shrink-0 font-mono text-mono text-ink-soft">
                {labels[task.status]}
              </span>
              <span className="min-w-0 break-words">{task.description}</span>
            </li>
          ))}
        </ul>
      ) : (
        <PanelEmpty>No tasks.</PanelEmpty>
      )}
      {payload.truncated ? (
        <p className="m-0 mt-lg text-ink-soft">Some tasks may not be shown.</p>
      ) : null}
    </section>
  );
}

function AutomationsContent({ payload }: { payload: AutomationsPayload }) {
  if (!payload.automations.length && !payload.truncated) {
    return <PanelEmpty>No automations report to this conversation.</PanelEmpty>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xl">
      {payload.automations.map((automation) => (
        <article key={automation.name} className="min-w-0 rounded-panel border border-edge p-lg">
          <div className="flex items-start gap-md">
            <div className="min-w-0 flex-1">
              <h2 className="m-0 break-all font-mono text-label font-strong">
                {automation.name}
              </h2>
              {automation.description ? (
                <p className="m-0 mt-sm break-words">{automation.description}</p>
              ) : null}
            </div>
            <span className="font-mono text-mono text-ink-soft">
              {automation.paused ? "Paused" : "Running"}
            </span>
          </div>
          <p className="m-0 mt-sm font-mono text-mono text-ink-soft">
            {automation.schedule} · Next <Moment at={automation.next_run_at} /> · Updated{" "}
            <Moment at={automation.updated_at} />
          </p>
          {automation.last_run_at ? (
            <p className="m-0 mt-xs font-mono text-mono text-ink-soft">
              Last <Moment at={automation.last_run_at} />
              {automation.latest_status ? " · " + automation.latest_status : ""}
            </p>
          ) : null}
          {automation.latest_response ? (
            <p className="m-0 mt-sm break-words">{automation.latest_response}</p>
          ) : null}
        </article>
      ))}
      {payload.truncated ? (
        <p className="m-0 text-ink-soft">Some automations may not be shown.</p>
      ) : null}
    </div>
  );
}

function SitesContent({ payload }: { payload: SitesPayload }) {
  if (!payload.sites.length && !payload.truncated) {
    return <PanelEmpty>No sites hosted from this conversation.</PanelEmpty>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xl">
      {payload.sites.map((site) => (
        <article key={site.name} className="min-w-0 rounded-panel border border-edge p-lg">
          <div className="flex items-start gap-md">
            <div className="min-w-0 flex-1">
              <h2 className="m-0 break-all font-mono text-label font-strong">{site.name}</h2>
              <p className="m-0 mt-xs font-mono text-mono text-ink-soft">
                {site.visibility} · Created <Moment at={site.created_at} /> · Updated{" "}
                <Moment at={site.updated_at} />
              </p>
            </div>
            <a href={site.url} target="_blank" rel="noreferrer" className={TAP_FLOOR}>
              Open site
            </a>
          </div>
        </article>
      ))}
      {payload.truncated ? (
        <p className="m-0 text-ink-soft">Some sites may not be shown.</p>
      ) : null}
    </div>
  );
}

function ArtifactsContent({ payload }: { payload: ArtifactsPayload }) {
  if (!payload.artifacts.length && !payload.truncated) {
    return <PanelEmpty>No artifacts shared in this conversation.</PanelEmpty>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xl">
      {payload.artifacts.map((artifact, index) => (
        <SharedArtifact
          key={[artifact.created_at, artifact.filename, index].join(":")}
          artifact={artifact}
        />
      ))}
      {payload.truncated ? (
        <p className="m-0 text-ink-soft">Some artifacts may not be shown.</p>
      ) : null}
    </div>
  );
}

function SharedArtifact({ artifact }: { artifact: ConversationArtifact }) {
  const [opened, setOpened] = useState(false);
  const textUrl = isTextMedia(artifact.media_type) ? artifact.url : null;
  const markdown = artifact.media_type === "text/markdown";
  return (
    <article className="min-w-0 rounded-panel border border-edge p-lg">
      {artifact.preview ? (
        <img
          loading="lazy"
          alt=""
          src={artifact.preview.url}
          className="mb-md max-h-(--media-card) max-w-full rounded-sm border border-edge object-contain"
        />
      ) : null}
      <h2 className="m-0 break-all font-mono text-label font-strong">
        {artifact.url ? (
          <a href={artifact.url} download={artifact.filename} className={TAP_FLOOR}>
            {artifact.filename}
          </a>
        ) : (
          artifact.filename
        )}
      </h2>
      {artifact.subject ? <p className="m-0 mt-sm break-words">{artifact.subject}</p> : null}
      <p className="m-0 mt-sm font-mono text-mono text-ink-soft">
        {artifact.media_type} · {formatSize(artifact.size_bytes)} ·{" "}
        <Moment at={artifact.created_at} />
      </p>
      {textUrl && markdown ? (
        <div className="mt-sm">
          <ArtifactText
            url={textUrl}
            name={artifact.filename}
            mediaType={artifact.media_type}
            display={opened ? "inline" : "excerpt"}
          />
          <button
            type="button"
            className={cn(
              "mt-sm border-0 bg-transparent p-0 text-label text-inherit underline",
              TAP_FLOOR,
            )}
            aria-expanded={opened}
            onClick={() => setOpened((full) => !full)}
          >
            {opened ? "First lines" : "Full document"}
          </button>
        </div>
      ) : textUrl ? (
        <details className="mt-sm" onToggle={(event) => setOpened(event.currentTarget.open)}>
          <summary className="cursor-pointer select-none text-label text-ink-soft">
            Preview
          </summary>
          <div className="mt-sm">
            {opened ? (
              <ArtifactText
                url={textUrl}
                name={artifact.filename}
                mediaType={artifact.media_type}
              />
            ) : null}
          </div>
        </details>
      ) : null}
    </article>
  );
}

function ChangedFile({ change }: { change: Change }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-panel border border-edge">
      <div className="border-b border-edge bg-fill px-lg py-sm">
        <h2 className="m-0 break-all font-mono text-label font-strong">{change.path}</h2>
      </div>
      <div className="overflow-x-auto">
        <pre className="m-0 min-w-max font-mono text-mono">
          {change.patch.split("\n").map((line, index) => (
            <span
              key={index}
              className={cn(
                "block px-md",
                index > 1 && line.startsWith("+") && "bg-affirm",
                index > 1 && line.startsWith("-") && "bg-attention",
                line.startsWith("@@") && "bg-fill",
              )}
            >
              {line || " "}
            </span>
          ))}
        </pre>
      </div>
      {change.truncated ? (
        <p className="m-0 border-t border-edge px-lg py-sm text-ink-soft">
          This diff is truncated.
        </p>
      ) : null}
    </section>
  );
}
