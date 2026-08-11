/** An agent as the boot read names it — the set a member may open and message. */
export type Agent = {
  id: string;
  name: string;
  model: string;
  main: boolean;
};

/** The same agent as the administration read names it, carrying the deploy facts only an admin
 *  sees. The boot payload does not send these, so a member-facing view cannot reach for them. */
export type AdminAgent = Agent & {
  internet_access_allowed: boolean;
  installations: string[];
  web_audience: string[];
};

export type Subagent = { name: string; model: string | null };

export type Member = { id?: string; email: string; admin: boolean; seated?: boolean };

export type Conversation = {
  id: string;
  surface: string;
  member_email: string | null;
  description: string;
  speakers: string[];
  turn_count: number;
  created_at: string;
  last_turn_at: string | null;
  readable: boolean;
  disclosable: boolean;
};

/** What the create act draws its form from: the `agent` kind's own spec schema and the model ids
 *  this deploy serves. The boot read carries it for a workspace admin and null for everyone else —
 *  the kind admits a create from nobody else, and an act that cannot land is not drawn. */
export type NewAgentForm = {
  spec_schema: { properties?: Record<string, SchemaProperty>; required?: string[] };
  models: string[];
};

export type AgentsPayload = {
  agents: Agent[];
  subagents: Subagent[];
  member: Member;
  new_agent: NewAgentForm | null;
};

export type SeatSummary = { limit: number | null; included: number | null };

export type SpendCap = {
  scope: string;
  subject: string | null;
  window_seconds: number;
  limit_micro_usd: number;
  on_breach: string;
};

export type DeployExtension = { name: string; version: string; sandbox_internet: boolean };

export type AdminPayload = {
  agents: AdminAgent[];
  members: Member[];
  seats: SeatSummary;
  caps: SpendCap[];
  deploy: { sandbox_internet: boolean; extensions: DeployExtension[] };
};

export type ChatFile = { filename: string; url: string | null; size_bytes: number };

export type QuestionOption = { label: string; description?: string };

export type QuestionEntry = {
  question: string;
  header?: string;
  options?: QuestionOption[];
  multi_select?: boolean;
  free_text_only?: boolean;
  allow_attachments?: boolean;
};

export type ChatQuestion = {
  turn_id: string;
  title?: string;
  questions: QuestionEntry[];
  answered?: number[];
};

export type CredentialPrompt = { slot: string; prompt: string; stored?: boolean };

export type CredentialRequest = {
  sealed: string;
  reason: string;
  prompts: CredentialPrompt[];
};

export type ToolEvent = {
  kind: "tool" | "skill";
  name: string;
  preview: string;
  description: string;
};

export type SubagentRun = { profile: string; conversation_id: string };

export type Message = {
  role: string;
  text: string;
  events?: ToolEvent[];
  subagents?: SubagentRun[];
};

export type Transcript = {
  messages: Message[];
  turn?: string;
  question?: ChatQuestion | null;
  credentials?: CredentialRequest | null;
  files?: ChatFile[] | null;
};

export type SchemaProperty = {
  type?: string;
  title?: string;
  format?: string;
  maxLength?: number;
  examples?: string[];
  enum?: string[];
  anyOf?: { type?: string; format?: string; maxLength?: number; enum?: string[] }[];
};
