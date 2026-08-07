import { useState } from "react";

import { Panel, PanelEmpty, usePanelRead } from "@/kernel/panel";
import { BASE } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Markdown } from "@/lib/markdown";
import { day } from "@/lib/moments";
import type { Agent } from "@/lib/types";
import { formatSize } from "@/views/Chat";

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
type ConversationFile = {
  path: string;
  size_bytes: number;
  modified_at: string;
  preview: ImagePreview | null;
};
type FilesPayload = { type: "files"; files: ConversationFile[]; truncated: boolean };
type ImagePreview = {
  type: "image";
  media_type: "image/gif" | "image/jpeg" | "image/png" | "image/webp";
  url: string;
};
export type PortalIcon = "artifact" | "calendar" | "diff" | "file" | "link" | "task";
type Source = { url: string; title: string; snippet: string; published_date: string | null };
type SourcesPayload = { type: "sources"; sources: Source[]; truncated: boolean };
type TaskStatus = "pending" | "in_progress" | "completed";
type ConversationTask = { description: string; status: TaskStatus };
type TasksPayload = {
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
  | FilesPayload
  | SitesPayload
  | SourcesPayload
  | TasksPayload;

const MAX_FILE_PREVIEW_BYTES = 256 * 1024;

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

function filePath(
  agentId: string,
  conversationId: string,
  path: string,
  rootConversationId?: string,
) {
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  return (
    "/agents/" +
    agentId +
    "/conversations/" +
    conversationId +
    "/files/" +
    encoded +
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
  agent: Agent;
  conversationId: string;
  slot: string;
  rootConversationId?: string;
  summary?: ConversationSlotSummary;
  embedded?: boolean;
  onClose?: () => void;
  onOpenAgent?: (agentId: string) => void;
}) {
  const [reloads, setReloads] = useState(0);
  const inventory = usePanelRead<ConversationSlotsPayload>(
    summary ? null : slotsPath(agent.id, conversationId, rootConversationId),
  );
  const state = usePanelRead<SlotPayload>(
    slotPath(agent.id, conversationId, slot, rootConversationId),
    reloads,
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
            {agent.name}
          </button>
        ) : null}
        <span className="inline-flex items-center gap-xs font-mono text-mono opacity-(--muted-strong)">
          {resolved ? <SlotIcon icon={resolved.icon} /> : null}
          {label}
        </span>
        <button type="button" className="ml-auto" onClick={() => setReloads((count) => count + 1)}>
          Refresh
        </button>
        {onClose ? (
          <button type="button" aria-label="Close slot" onClick={onClose}>
            Close
          </button>
        ) : null}
      </div>
      <div className="flex-1 overflow-y-auto p-xl">
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
          {(payload) => (
            <SlotContent
              payload={payload}
              agentId={agent.id}
              conversationId={conversationId}
              rootConversationId={rootConversationId}
            />
          )}
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
  return <main className="flex min-h-0 min-w-0 flex-col">{content}</main>;
}

export function SlotIcon({ icon }: { icon: PortalIcon }) {
  const path = {
    artifact: "M4 7 12 3l8 4-8 4-8-4Zm0 5 8 4 8-4M4 17l8 4 8-4",
    calendar: "M6 3v4M18 3v4M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z",
    diff: "M8 5v6M5 8h6M14 8h5M5 17h6M14 17h5",
    file: "M6 2h8l4 4v16H6V2Zm8 0v5h5",
    link: "M9 15l6-6M7.5 17.5l-1 1a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 5 0M16.5 6.5l1-1a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-5 0",
    task: "M9 6h11M9 12h11M9 18h11M3.5 6l1 1 2-2M3.5 12l1 1 2-2M3.5 18l1 1 2-2",
  }[icon];
  return (
    <svg
      aria-hidden
      data-slot-icon={icon}
      viewBox="0 0 24 24"
      className="h-[1em] w-[1em] shrink-0 fill-none stroke-current"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={path} />
    </svg>
  );
}

function SlotContent({
  payload,
  agentId,
  conversationId,
  rootConversationId,
}: {
  payload: SlotPayload;
  agentId: string;
  conversationId: string;
  rootConversationId?: string;
}) {
  if (payload.type === "changes") {
    if (!payload.changes.length && !payload.truncated) return <PanelEmpty>No changes.</PanelEmpty>;
    return (
      <div className="flex flex-col gap-xl">
        {payload.changes.map((change, index) => (
          <ChangedFile key={[change.path, index].join(":")} change={change} />
        ))}
        {payload.truncated ? (
          <p className="m-0 opacity-(--muted-soft)">Some changes may not be shown.</p>
        ) : null}
      </div>
    );
  }
  if (payload.type === "files") {
    return (
      <FilesContent
        payload={payload}
        agentId={agentId}
        conversationId={conversationId}
        rootConversationId={rootConversationId}
      />
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
          <h2 className="m-0 text-label font-strong">
            <a href={source.url} target="_blank" rel="noreferrer">
              {source.title || source.url}
            </a>
          </h2>
          {source.published_date ? (
            <p className="m-0 mt-xs font-mono text-mono opacity-(--muted-strong)">
              {source.published_date}
            </p>
          ) : null}
          {source.snippet ? (
            <details className="mt-sm">
              <summary className="cursor-pointer select-none text-label opacity-(--muted-strong)">
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
        <p className="m-0 opacity-(--muted-soft)">Some sources may not be shown.</p>
      ) : null}
    </div>
  );
}

function TasksContent({ payload }: { payload: TasksPayload }) {
  const labels: Record<TaskStatus, string> = {
    pending: "Pending",
    in_progress: "In progress",
    completed: "Completed",
  };
  return (
    <section className="min-w-0">
      {payload.title ? <h2 className="m-0 text-title font-strong">{payload.title}</h2> : null}
      <p className="m-0 mt-sm font-mono text-mono opacity-(--muted-strong)">
        {payload.completed_count} of {payload.total_count} completed.
      </p>
      {payload.tasks.length ? (
        <ul className="m-0 mt-lg flex list-none flex-col gap-sm p-0">
          {payload.tasks.map((task, index) => (
            <li
              key={[task.description, index].join(":")}
              className="flex min-w-0 items-start gap-md rounded-panel border border-edge p-lg"
            >
              <span className="shrink-0 font-mono text-mono opacity-(--muted-strong)">
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
        <p className="m-0 mt-lg opacity-(--muted-soft)">Some tasks may not be shown.</p>
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
            <span className="font-mono text-mono opacity-(--muted-strong)">
              {automation.paused ? "Paused" : "Running"}
            </span>
          </div>
          <p className="m-0 mt-sm font-mono text-mono opacity-(--muted-strong)">
            {automation.schedule} · Next {day(automation.next_run_at)} · Updated{" "}
            {day(automation.updated_at)}
          </p>
          {automation.last_run_at ? (
            <p className="m-0 mt-xs font-mono text-mono opacity-(--muted-strong)">
              Last {day(automation.last_run_at)}
              {automation.latest_status ? " · " + automation.latest_status : ""}
            </p>
          ) : null}
          {automation.latest_response ? (
            <p className="m-0 mt-sm break-words">{automation.latest_response}</p>
          ) : null}
        </article>
      ))}
      {payload.truncated ? (
        <p className="m-0 opacity-(--muted-soft)">Some automations may not be shown.</p>
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
              <p className="m-0 mt-xs font-mono text-mono opacity-(--muted-strong)">
                {site.visibility} · Created {day(site.created_at)} · Updated {day(site.updated_at)}
              </p>
            </div>
            <a href={site.url} target="_blank" rel="noreferrer">
              Open site
            </a>
          </div>
        </article>
      ))}
      {payload.truncated ? (
        <p className="m-0 opacity-(--muted-soft)">Some sites may not be shown.</p>
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
        <article
          key={[artifact.created_at, artifact.filename, index].join(":")}
          className="min-w-0 rounded-panel border border-edge p-lg"
        >
          {artifact.preview ? (
            <img
              loading="lazy"
              alt=""
              src={artifact.preview.url}
              className="mb-md max-h-[20rem] max-w-full rounded-sm border border-edge object-contain"
            />
          ) : null}
          <h2 className="m-0 break-all font-mono text-label font-strong">
            {artifact.url ? (
              <a href={artifact.url} download={artifact.filename}>
                {artifact.filename}
              </a>
            ) : (
              artifact.filename
            )}
          </h2>
          {artifact.subject ? <p className="m-0 mt-sm break-words">{artifact.subject}</p> : null}
          <p className="m-0 mt-sm font-mono text-mono opacity-(--muted-strong)">
            {artifact.media_type} · {formatSize(artifact.size_bytes)} · {day(artifact.created_at)}
          </p>
        </article>
      ))}
      {payload.truncated ? (
        <p className="m-0 opacity-(--muted-soft)">Some artifacts may not be shown.</p>
      ) : null}
    </div>
  );
}

function FilesContent({
  payload,
  agentId,
  conversationId,
  rootConversationId,
}: {
  payload: FilesPayload;
  agentId: string;
  conversationId: string;
  rootConversationId?: string;
}) {
  const [preview, setPreview] = useState<
    | { type: "image"; path: string; image: ImagePreview }
    | { type: "text"; path: string; text: string }
    | null
  >(null);

  async function view(file: ConversationFile) {
    if (file.preview) {
      setPreview({ type: "image", path: file.path, image: file.preview });
      return;
    }
    setPreview({ type: "text", path: file.path, text: "Reading…" });
    const path = filePath(agentId, conversationId, file.path, rootConversationId);
    try {
      const response = await fetch(BASE + path, { credentials: "same-origin" });
      setPreview({
        type: "text",
        path: file.path,
        text: response.ok ? await response.text() : "Error " + response.status + " — retry.",
      });
    } catch {
      setPreview({ type: "text", path: file.path, text: "Network error — retry." });
    }
  }

  if (!payload.files.length && !payload.truncated) {
    return <PanelEmpty>No files in this conversation's workspace.</PanelEmpty>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-xl">
      <ul className="m-0 flex list-none flex-col gap-sm p-0">
        {payload.files.map((file) => {
          const path = filePath(agentId, conversationId, file.path, rootConversationId);
          return (
            <li key={file.path} className="min-w-0 rounded-panel border border-edge p-lg">
              <div className="flex min-w-0 items-start gap-md">
                {file.preview ? (
                  <img
                    loading="lazy"
                    alt={"Thumbnail of " + file.path}
                    src={file.preview.url}
                    className="h-16 w-16 shrink-0 rounded-sm border border-edge object-cover"
                  />
                ) : null}
                <div className="min-w-0 flex-1">
                  <button
                    type="button"
                    className="max-w-full border-0 bg-transparent p-0 text-left font-mono text-label font-strong text-inherit underline disabled:no-underline"
                    disabled={!file.preview && file.size_bytes > MAX_FILE_PREVIEW_BYTES}
                    onClick={() => view(file)}
                  >
                    <span className="block break-all">{file.path}</span>
                  </button>
                  <p className="m-0 mt-xs font-mono text-mono opacity-(--muted-strong)">
                    {formatSize(file.size_bytes)} · {day(file.modified_at)}
                  </p>
                </div>
                <a href={BASE + path} download={file.path.split("/").at(-1)}>
                  Download
                </a>
              </div>
            </li>
          );
        })}
      </ul>
      {preview ? (
        <section className="min-w-0 overflow-hidden rounded-panel border border-edge">
          <h2 className="m-0 break-all border-b border-edge bg-fill-subtle px-lg py-sm font-mono text-label font-strong">
            {preview.path}
          </h2>
          {preview.type === "image" ? (
            <div className="flex max-h-[40rem] justify-center overflow-auto p-lg">
              <img
                loading="lazy"
                alt={"Preview of " + preview.path}
                src={preview.image.url}
                className="max-h-[36rem] max-w-full object-contain"
              />
            </div>
          ) : (
            <pre className="m-0 max-h-[32rem] overflow-auto whitespace-pre-wrap wrap-anywhere p-lg font-mono text-mono">
              {preview.text}
            </pre>
          )}
        </section>
      ) : null}
      {payload.truncated ? (
        <p className="m-0 opacity-(--muted-soft)">Some files may not be shown.</p>
      ) : null}
    </div>
  );
}

function ChangedFile({ change }: { change: Change }) {
  return (
    <section className="min-w-0 overflow-hidden rounded-panel border border-edge">
      <div className="border-b border-edge bg-fill-subtle px-lg py-sm">
        <h2 className="m-0 break-all font-mono text-label font-strong">{change.path}</h2>
      </div>
      <div className="overflow-x-auto">
        <pre className="m-0 min-w-max font-mono text-mono">
          {change.patch.split("\n").map((line, index) => (
            <span
              key={index}
              className={cn(
                "block px-md",
                index > 1 && line.startsWith("+") && "bg-link/10",
                index > 1 && line.startsWith("-") && "bg-attention/25",
                line.startsWith("@@") && "bg-fill-subtle",
              )}
            >
              {line || " "}
            </span>
          ))}
        </pre>
      </div>
      {change.truncated ? (
        <p className="m-0 border-t border-edge px-lg py-sm opacity-(--muted-soft)">
          This diff is truncated.
        </p>
      ) : null}
    </section>
  );
}
