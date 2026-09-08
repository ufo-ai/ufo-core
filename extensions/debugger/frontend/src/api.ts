export type ConversationSummary = {
  id: string;
  surface: string;
  queue_key: string;
  member_email: string | null;
  created_at: string;
  turn_count: number;
  last_turn_at: string | null;
  opening_message: string | null;
};

export type TerminalFrame = {
  status: string;
  text: string;
  error_class: string | null;
  error_message: string | null;
  tokens: number;
  cost_micro_usd: number;
  cache_percent: number;
  model: string;
  reasoning: string | null;
};

export type Turn = {
  id: string;
  workspace_id: string;
  conversation_id: string;
  agent_id: string;
  seq: number;
  status: string;
  inbound: string;
  created_at: string;
  updated_at: string | null;
  admission_source: string;
  speaker_member_id: string | null;
  context: { sender?: string | null; timezone?: string | null; source?: string | null } | null;
  terminal: TerminalFrame | null;
  parent_turn_id: string | null;
  subagent_profile: string | null;
  traceparent: string | null;
};

export type LedgerEntry = {
  dimension: string;
  amount: number;
  priced_micro_usd: number;
  model: string;
  created_at: string;
};

export type TurnStep = {
  number: number;
  kind: "model" | "tool" | "workflow";
  name: string;
  function_name: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  messages: TranscriptMessage[];
  usage: StepUsage | null;
};

export type StepUsage = {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_5m_tokens: number;
  cache_write_30m_tokens: number;
  cache_write_1h_tokens: number;
};

export type TurnDetail = {
  turn: Turn;
  ledger: LedgerEntry[];
  children: Turn[];
};

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; source: unknown }
  | { type: "thinking"; thinking: string; signature: string }
  | { type: "redacted_thinking"; data: string }
  | { type: "reasoning"; id: string; encrypted_content: string; summary: string[] }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | {
      type: "tool_result";
      tool_use_id: string;
      content: string | ContentBlock[];
      is_error: boolean;
    };

export type TranscriptMessage = { role: "user" | "assistant"; content: string | ContentBlock[] };
export type Transcript = {
  seq: number;
  messages: TranscriptMessage[];
  system: string | null;
  injected: string | null;
};

export type PendingResult = {
  entry_id: number;
  call: string;
  arguments: string;
  text: string;
  truncated: boolean;
};

export type RecoveryRecord = {
  objective: string | null;
  user_inputs: string[];
  pending_results: PendingResult[];
  checklist: string[];
  checkpoint: string | null;
  handoff: string | null;
  history_path: string;
  history_lost: boolean;
  active_requests: string[];
  loaded_skills: string[];
  first_entry_id: number;
  last_entry_id: number;
};

export type RolloverRecord = {
  index: number;
  before: TranscriptMessage[];
  after: TranscriptMessage[];
  recovery: RecoveryRecord;
};

export type FleetWorkspace = {
  workspace_id: string;
  domain: string | null;
  members: number;
  conversations: number;
  last_turn_at: string | null;
};

export type FleetThread = {
  workspace_id: string;
  domain: string | null;
  conversation_id: string;
  surface: string;
  queue_key: string;
  title: string | null;
  turn_count: number;
  last_turn_at: string;
};

export type FleetListing = { workspaces: FleetWorkspace[]; threads: FleetThread[] };

export type WorkspaceFile = { path: string; size_bytes: number; modified_at: string };
export type WorkspaceMeta = {
  workspace_id: string;
  slack_team: string | null;
  datadog_site: string | null;
};

const base = window.location.pathname.replace(/\/$/, "");

export function apiUrl(path: string): string {
  const url = new URL(`${base}/api/${path}`, window.location.origin);
  const ws = new URLSearchParams(window.location.search).get("ws");
  if (ws) url.searchParams.set("ws", ws);
  return url.toString();
}

export async function get<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return (await response.json()) as T;
}

export function slackLink(team: string, queueKey: string): string {
  const [channel, thread] = queueKey.split(":", 2);
  const root = `https://app.slack.com/client/${team}/${channel}`;
  return thread ? `${root}/thread/${channel}-${thread}` : root;
}

export function money(microUsd: number): string {
  return `$${(microUsd / 1e6).toFixed(4)}`;
}

export function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function timestamp(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

export function duration(ms: number | null): string {
  if (ms === null) return "running";
  if (ms < 1_000) return `${ms.toLocaleString()} ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  const minutes = Math.floor(ms / 60_000);
  return `${minutes} min ${((ms % 60_000) / 1_000).toFixed(1)} s`;
}
