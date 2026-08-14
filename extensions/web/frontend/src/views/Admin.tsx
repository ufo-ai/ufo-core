import { useState } from "react";

import { Button, ConfirmButton } from "@/components/ui/button";
import { Facts } from "@/components/ui/facts";
import { Hint, Input } from "@/components/ui/field";
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
import { COLUMN, Pane } from "@/kernel/pane";
import { DataTable } from "@/kernel/table";
import { postIntent } from "@/lib/api";
import { money } from "@/lib/money";
import type { AdminAgent, AdminPayload, Member } from "@/lib/types";

const FRAME = COLUMN + " overflow-y-auto scrollbar-gutter-stable p-2xl";

function allowance(allowed: boolean): string {
  return allowed ? "Allowed" : "Blocked";
}

export function Admin() {
  const [reloads, setReloads] = useState(0);
  const [notice, setNotice] = useState<NoticeState>(QUIET);
  const state = usePanelRead<AdminPayload>("/api/admin", reloads);

  async function intent(agentId: string, envelope: unknown) {
    const outcome = await postIntent(agentId, envelope);
    setNotice(outcomeNotice(outcome));
    if (outcome.applied) setReloads((count) => count + 1);
  }

  return (
    <Panel
      state={state}
      loading={() => (
        <Pane data-testid="admin-loading" className={FRAME}>
          <PanelSkeleton shape="table" />
        </Pane>
      )}
      failed={(message) => (
        <Pane className={FRAME}>
          <PanelEmpty>{message}</PanelEmpty>
        </Pane>
      )}
    >
      {(payload) => {
        const mainAgent = payload.agents.find((agent) => agent.main);

        return (
          <Pane className={FRAME} data-testid="admin">
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
                    onAudience={(verb, email) => intent(agent.id, { verb, email })}
                  />
                )}
              </DataTable>
            </Section>

            <Section title="Members">
              <DataTable
                columns={["Member", "Role", "Seat", ""]}
                rows={payload.members}
                rowKey={(member) => member.email}
                empty="This workspace has no members yet."
              >
                {(member) => (
                  <MemberRow
                    member={member}
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
          </Pane>
        );
      }}
    </Panel>
  );
}

function AgentRow({
  agent,
  onAudience,
}: {
  agent: AdminAgent;
  onAudience: (verb: string, email: string) => void;
}) {
  const [email, setEmail] = useState("");
  return (
    <>
      <Td>
        {agent.name}
        {agent.main ? (
          <span className="ml-xs text-small opacity-(--opacity-muted-strong)">Main</span>
        ) : null}
      </Td>
      <Td>{agent.model}</Td>
      <Td>{allowance(agent.internet_access_allowed)}</Td>
      <Td>{agent.installations.join(", ") || "—"}</Td>
      <Td>{agent.main ? "Every member" : agent.web_audience.concat("admins").join(", ")}</Td>
      <Td>
        <div className="flex flex-wrap items-stretch gap-xs">
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
  onApply,
}: {
  member: Member;
  onApply: (spec: { admin: boolean; seated: boolean }) => void;
}) {
  return (
    <>
      <Td>{member.email}</Td>
      <Td>{member.admin ? "Admin" : "Member"}</Td>
      <Td>{member.seated ? "Seated" : "Unseated"}</Td>
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
