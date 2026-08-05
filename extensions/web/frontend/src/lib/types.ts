export type Agent = {
  id: string;
  name: string;
  model: string;
  main: boolean;
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
  turn_count: number;
  created_at: string;
  last_turn_at: string | null;
  readable: boolean;
  disclosable: boolean;
};

export type AgentsPayload = { agents: Agent[]; subagents: Subagent[]; member: Member };

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
  agents: Agent[];
  members: Member[];
  seats: SeatSummary;
  caps: SpendCap[];
  models: string[];
  reasoning_levels: string[];
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

export type Message = { role: string; text: string; events?: ToolEvent[] };

export type Transcript = {
  messages: Message[];
  question?: ChatQuestion | null;
  credentials?: CredentialRequest | null;
  files?: ChatFile[] | null;
};

export type SchemaProperty = {
  type?: string;
  title?: string;
  description?: string;
  enum?: string[];
  anyOf?: { type?: string; enum?: string[] }[];
};
