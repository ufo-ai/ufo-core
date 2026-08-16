/** An agent as the boot read names it — the set a member may open and message. */
export type Agent = {
  id: string;
  name: string;
  model: string;
  main: boolean;
  web_audience?: string[];
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

/** The agent a conversation ran under, carried by a read that spans every agent the member
 *  reaches and null by one taken inside a single agent's namespace. */
export type ConversationAgent = { id: string; name: string };

export type Conversation = {
  id: string;
  agent: ConversationAgent | null;
  surface: string;
  surface_label: string | null;
  audience: string;
  member_email: string | null;
  description: string;
  source: string | null;
  speakers: string[];
  turn_count: number;
  created_at: string;
  last_turn_at: string | null;
  readable: boolean;
  disclosable: boolean;
};

/** A conversation a read spanning every agent answers with: the row names the agent that ran it,
 *  since the pane it lands in names none. */
export type OwnedConversation = Conversation & { agent: ConversationAgent };

/** What the create act draws its form from: the `agent` kind's own spec schema and the model ids
 *  this deploy serves. The boot read carries it for every signed-in member — any speaking member
 *  may create an agent and owns what they created. */
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
  caps: SpendCap[];
  deploy: { sandbox_internet: boolean; extensions: DeployExtension[] };
};

/** A file a turn shared. `preview_url` is a same-origin picture of it, carried only when the file
 *  is itself an image — the chat draws those inline in the reply instead of as a card.
 *  `media_type` says which cards the artifacts sidebar can draw as a document, so pressing one
 *  opens it there instead of downloading. */
export type ChatFile = {
  filename: string;
  url: string | null;
  size_bytes: number;
  preview_url: string | null;
  media_type: string;
};

export type QuestionOption = { label: string; description?: string };

export type QuestionEntry = {
  question: string;
  header?: string;
  options?: QuestionOption[];
  multi_select?: boolean;
  free_text_only?: boolean;
  allow_attachments?: boolean;
};

/** What a turn asks of the member, named by the turn that asked. `answered` holds what the surface
 *  confirmed it admitted for each entry the member has submitted — the words the transcript will
 *  read back, so the entry states its answer rather than vanishing. */
export type ChatQuestion = {
  turn_id: string;
  title?: string;
  questions: QuestionEntry[];
  answered?: Record<number, string>;
};

export type CredentialPrompt = { slot: string; prompt: string; stored?: boolean };

export type CredentialRequest = {
  sealed: string;
  reason: string;
  prompts: CredentialPrompt[];
};

/** One thing an agent did, as a conversation states it: a tool it dispatched, a skill it mounted,
 *  or a line it wrote between the two. */
export type ActivityEvent =
  | { kind: "tool"; name: string; preview: string; description: string }
  | { kind: "skill"; name: string; preview: string; description: string }
  | { kind: "note"; text: string };

/** One subagent run beneath the reply that spawned it: the work it did, what it answered, and the
 *  runs it spawned in turn. `conversation_id` opens the whole record; `name` is the display name
 *  its spawn gave it, and the row states the profile when there is none. `turn_id`,
 *  `parent_turn_id`, `running`, and `current` exist only on a row the live stream is building —
 *  the durable transcript states finished runs and carries none of them. */
export type SubagentRun = {
  profile: string;
  name?: string;
  conversation_id: string;
  events: ActivityEvent[];
  output: string;
  subagents: SubagentRun[];
  turn_id?: string;
  parent_turn_id?: string;
  running?: boolean;
  current?: string;
  /** How many of the spawning turn's events had landed when this row appeared — where the row
   *  interleaves into the activity list. Live-only; a durable run renders after the events. */
  at?: number;
};

/** One message a conversation states. `arrival_id` names the inbound-queue row a message admitted
 *  while a turn ran landed on, and it is carried only while that turn has not taken the message up
 *  — the turn's `absorbed` event names the same id when it does. `speaker` is the display line the
 *  admitting surface reported for a member other than the viewer, so a bubble in a conversation
 *  more members are in names who said it. */
export type Message = {
  role: string;
  text: string;
  speaker?: string;
  /** The question these words answered, drawn over them so the answer reads with what it
   *  answered. */
  asked?: string;
  arrival_id?: string;
  events?: ActivityEvent[];
  subagents?: SubagentRun[];
  /** What this reply asked the member, carried by the reply that asked it. */
  question?: ChatQuestion;
  /** What this reply's turn shared, carried by the reply so the files stay where the words
   *  that shared them are. */
  files?: ChatFile[];
};

export type Transcript = {
  messages: Message[];
  turn?: string;
  credentials?: CredentialRequest | null;
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
