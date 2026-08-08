import { useEffect, useState, type FormEvent } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Checkbox, Field, FieldGroup, Hint, Input, Label, Textarea } from "@/components/ui/field";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Td } from "@/components/ui/table";
import {
  type NoticeState,
  OutcomeNotice,
  Panel,
  PanelEmpty,
  PanelSkeleton,
  QUIET,
  Section,
  outcomeNotice,
  usePanelRead,
} from "@/kernel/panel";
import { DataTable } from "@/kernel/table";
import { getJson, postIntent } from "@/lib/api";
import { money } from "@/lib/money";
import type { AdminAgent, AdminPayload, Member } from "@/lib/types";

const FRAME = "flex flex-col overflow-y-auto p-2xl";

function allowance(allowed: boolean): string {
  return allowed ? "Allowed" : "Blocked";
}

/** What the plan grants, stated as a line rather than as more words on the section's own heading —
 *  a heading that grew a clause per plan field stops naming the records under it. */
function seatLine(seats: AdminPayload["seats"]): string {
  if (seats.limit === null && seats.included === null) return "Seats are not limited on this plan.";
  const limit = seats.limit === null ? null : seats.limit + " seats";
  const included = seats.included === null ? null : seats.included + " included in the plan";
  return [limit, included].filter(Boolean).join(", ") + ".";
}

export function Admin() {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const [copying, setCopying] = useState<AdminAgent | null>(null);
  const state = usePanelRead<AdminPayload>("/api/admin", reloads);

  async function intent(agentId: string, envelope: unknown, prefix?: string) {
    const outcome = await postIntent(agentId, envelope);
    setNotice(
      prefix && outcome.applied
        ? { text: prefix + " " + outcome.message, refused: false }
        : outcomeNotice(outcome),
    );
    if (outcome.applied) setReloads((count) => count + 1);
  }

  return (
    <Panel
      state={state}
      loading={() => (
        <main data-testid="admin-loading" className={FRAME}>
          <PanelSkeleton shape="table" />
        </main>
      )}
      failed={(message) => (
        <main className={FRAME}>
          <PanelEmpty>{message}</PanelEmpty>
        </main>
      )}
    >
      {(payload) => {
        const mainAgent = payload.agents.find((agent) => agent.main);
        const gated = payload.seats.limit !== null || payload.seats.included !== null;

        return (
          <main className={FRAME} data-testid="admin">
            <h1 className="m-0 mb-2xl text-title font-strong">Administration</h1>
            <OutcomeNotice state={notice} />

            <Section title="Agents">
              <DataTable
                columns={["Agent", "Model", "Public Internet", "Surfaces", "Web Audience", ""]}
                rows={payload.agents}
                rowKey={(agent) => agent.id}
                empty="This workspace has no agents."
              >
                {(agent) => (
                  <AgentRow
                    agent={agent}
                    onCopy={() => {
                      setCopying(agent);
                      setNotice({
                        text: "Copying " + agent.name + " — configuration only.",
                        refused: false,
                      });
                    }}
                    onAudience={(verb, email) => intent(agent.id, { verb, email })}
                  />
                )}
              </DataTable>
            </Section>

            {mainAgent ? (
              <CreateAgent
                payload={payload}
                copying={copying}
                onCreated={(name, spec) =>
                  intent(
                    mainAgent.id,
                    { verb: "apply", kind: "agent", name, spec },
                    "Created " + name + ".",
                  )
                }
              />
            ) : null}

            <Section title="Members">
              <Hint className="m-0">{seatLine(payload.seats)}</Hint>
              <DataTable
                columns={gated ? ["Member", "Role", "Seat", ""] : ["Member", "Role", ""]}
                rows={payload.members}
                rowKey={(member) => member.email}
                empty="This workspace has no members yet."
              >
                {(member) => (
                  <MemberRow
                    member={member}
                    gated={gated}
                    onApply={(spec) =>
                      mainAgent
                        ? intent(mainAgent.id, {
                            verb: "apply",
                            kind: "member",
                            name: member.id,
                            spec,
                          })
                        : undefined
                    }
                  />
                )}
              </DataTable>
            </Section>

            <Section title="Billing">
              <DataTable
                columns={["Cap", "Subject", "Window", "Limit", "On Breach"]}
                rows={payload.caps}
                rowKey={(cap) => cap.scope + "/" + cap.subject + "/" + cap.window_seconds}
                empty="No spend caps are set."
              >
                {(cap) => (
                  <>
                    <Td>{cap.scope}</Td>
                    <Td>{cap.subject || "—"}</Td>
                    <Td>{cap.window_seconds / 3600 + "h"}</Td>
                    <Td>{money(cap.limit_micro_usd)}</Td>
                    <Td>{cap.on_breach}</Td>
                  </>
                )}
              </DataTable>
              <Hint className="m-0">
                The plan, invoices, and payment methods are managed with the agent in chat.
              </Hint>
            </Section>

            <Section title="Deploy">
              <Facts
                rows={[
                  {
                    label: "Sandbox public internet",
                    value: allowance(payload.deploy.sandbox_internet),
                  },
                ]}
              />
              <DataTable
                columns={["Extension", "Version", "Public Internet"]}
                rows={payload.deploy.extensions}
                rowKey={(extension) => extension.name}
                empty="This deploy installs no extensions."
              >
                {(extension) => (
                  <>
                    <Td>{extension.name}</Td>
                    <Td>{extension.version}</Td>
                    <Td>{allowance(extension.sandbox_internet)}</Td>
                  </>
                )}
              </DataTable>
            </Section>
          </main>
        );
      }}
    </Panel>
  );
}

function AgentRow({
  agent,
  onCopy,
  onAudience,
}: {
  agent: AdminAgent;
  onCopy: () => void;
  onAudience: (verb: string, email: string) => void;
}) {
  const [email, setEmail] = useState("");
  return (
    <>
      <Td>
        {agent.name}
        {agent.main ? (
          <span className="ml-xs text-small opacity-(--muted-strong)">Main</span>
        ) : null}
      </Td>
      <Td>{agent.model}</Td>
      <Td>{allowance(agent.internet_access_allowed)}</Td>
      <Td>{agent.installations.join(", ") || "—"}</Td>
      <Td>{agent.main ? "Every member" : agent.web_audience.concat("admins").join(", ")}</Td>
      <Td>
        <div className="flex flex-wrap items-stretch gap-xs">
          <Button variant="row" onClick={onCopy}>
            Copy
          </Button>
          {agent.main ? null : (
            <>
              <Input
                type="email"
                aria-label={"Web access address for " + agent.name}
                placeholder="email@work.com"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="max-w-control-row"
              />
              <Button
                variant="row"
                onClick={() => email.trim() && onAudience("grant_web_access", email.trim())}
              >
                Grant
              </Button>
              <Button
                variant="row"
                onClick={() => email.trim() && onAudience("revoke_web_access", email.trim())}
              >
                Revoke
              </Button>
            </>
          )}
        </div>
      </Td>
    </>
  );
}

function MemberRow({
  member,
  gated,
  onApply,
}: {
  member: Member;
  gated: boolean;
  onApply: (spec: { admin: boolean; seated: boolean }) => void;
}) {
  return (
    <>
      <Td>{member.email}</Td>
      <Td>{member.admin ? "Admin" : "Member"}</Td>
      {gated ? <Td>{member.seated ? "Seated" : "—"}</Td> : null}
      <Td>
        <div className="flex flex-wrap gap-xs">
          {member.admin ? (
            <ConfirmButton
              verb="Remove admin"
              variant="row"
              onClick={() => onApply({ admin: false, seated: Boolean(member.seated) })}
            />
          ) : (
            <Button
              variant="row"
              onClick={() => onApply({ admin: true, seated: Boolean(member.seated) })}
            >
              Make admin
            </Button>
          )}
          {member.seated ? (
            <ConfirmButton
              verb="Unseat"
              variant="row"
              onClick={() => onApply({ admin: member.admin, seated: false })}
            />
          ) : (
            <Button variant="row" onClick={() => onApply({ admin: member.admin, seated: true })}>
              Seat
            </Button>
          )}
        </div>
      </Td>
    </>
  );
}

function CreateAgent({
  payload,
  copying,
  onCreated,
}: {
  payload: AdminPayload;
  copying: AdminAgent | null;
  onCreated: (name: string, spec: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  const [model, setModel] = useState(copying?.model ?? payload.models[0]);
  const [reasoning, setReasoning] = useState(payload.reasoning_levels[0]);
  const [internet, setInternet] = useState(copying ? copying.internet_access_allowed : true);
  const [prompt, setPrompt] = useState("");

  useEffect(() => {
    if (!copying) return;
    setModel(copying.model);
    setInternet(copying.internet_access_allowed);
    let live = true;
    getJson<{ agent: { prompt: string }; spec: { reasoning: string } }>(
      "/agents/" + copying.id + "/overview",
    ).then((result) => {
      if (!live || !result.ok) return;
      setPrompt(result.payload.agent.prompt);
      setReasoning(result.payload.spec.reasoning);
    });
    return () => {
      live = false;
    };
  }, [copying]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onCreated(name.trim(), {
      model,
      internet_access_allowed: internet,
      reasoning,
      prompt,
    });
  }

  return (
    <Section title="Create agent">
      <FieldGroup
        onSubmit={submit}
        submit={
          <Button type="submit" variant="send">
            Create
          </Button>
        }
      >
        <Field label="Name" htmlFor="new-agent-name">
          <Input
            id="new-agent-name"
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>
        <Field label="Model" htmlFor="new-agent-model">
          <Select value={model} onValueChange={setModel}>
            <SelectTrigger id="new-agent-model">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {payload.models.map((id) => (
                <SelectItem key={id} value={id}>
                  {id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Reasoning" htmlFor="new-agent-reasoning">
          <Select value={reasoning} onValueChange={setReasoning}>
            <SelectTrigger id="new-agent-reasoning">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {payload.reasoning_levels.map((level) => (
                <SelectItem key={level} value={level}>
                  {level}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Label htmlFor="new-agent-internet" className="flex items-center gap-sm font-inherit">
          <Checkbox
            id="new-agent-internet"
            checked={internet}
            onChange={(event) => setInternet(event.target.checked)}
          />
          Public internet
        </Label>
        <Field label="System prompt" htmlFor="new-agent-prompt">
          <Textarea
            id="new-agent-prompt"
            required
            rows={4}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
          />
        </Field>
      </FieldGroup>
    </Section>
  );
}
